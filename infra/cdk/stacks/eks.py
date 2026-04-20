"""EKS stack for OpenClaw SaaS (China region)"""
import aws_cdk as cdk
from aws_cdk import aws_eks as eks
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_iam as iam
from aws_cdk.lambda_layer_kubectl_v30 import KubectlV30Layer
from constructs import Construct


def sts_audience(partition: str) -> str:
    """Return STS audience for OIDC trust — .cn suffix for China partition."""
    if partition == "aws-cn":
        return "sts.amazonaws.com.cn"
    return "sts.amazonaws.com"


class EksStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        config,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        partition = self.partition  # "aws-cn" in China
        sts_aud = sts_audience(partition)

        # Determine Kubernetes version
        k8s_version_map = {
            "1.28": eks.KubernetesVersion.V1_28,
            "1.29": eks.KubernetesVersion.V1_29,
            "1.30": eks.KubernetesVersion.V1_30,
            "1.31": eks.KubernetesVersion.V1_31,
        }
        k8s_version = k8s_version_map.get(config.eks_version, eks.KubernetesVersion.V1_30)

        # Create EKS cluster
        self.cluster = eks.Cluster(
            self,
            "OpenClawCluster",
            cluster_name=f"{config.resource_prefix}-cluster",
            version=k8s_version,
            kubectl_layer=KubectlV30Layer(self, "KubectlLayer"),
            vpc=vpc,
            vpc_subnets=[ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)],
            default_capacity=0,
            endpoint_access=eks.EndpointAccess.PUBLIC_AND_PRIVATE,
        )

        # Add Graviton managed node group (ARM64) with configurable params
        self.nodegroup = self.cluster.add_nodegroup_capacity(
            "GravitonNodes",
            instance_types=[ec2.InstanceType(config.eks_node_instance_type)],
            min_size=config.eks_node_min,
            max_size=config.eks_node_max,
            desired_size=config.eks_node_desired,
            disk_size=config.eks_node_disk_size,
            ami_type=eks.NodegroupAmiType.AL2_ARM_64,
        )

        # Store node security group for RDS access
        self.node_security_group = self.cluster.cluster_security_group

        # Allow VPC CIDR → TCP:8000 on the cluster security group.
        # NLB IP-mode forwards traffic directly to pod IPs on port 8000;
        # without this rule the EKS cluster SG (self-referencing only by
        # default) blocks the NLB health-check and data-plane traffic.
        self.cluster.cluster_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(8000),
            description="Allow NLB to reach platform-api pods on port 8000",
        )

        # EBS CSI Driver IRSA — use CfnJson to handle OIDC token resolution
        oidc_provider = self.cluster.open_id_connect_provider

        conditions = cdk.CfnJson(
            self, "EbsCsiCondition",
            value={
                f"{oidc_provider.open_id_connect_provider_issuer}:sub":
                    "system:serviceaccount:kube-system:ebs-csi-controller-sa",
                f"{oidc_provider.open_id_connect_provider_issuer}:aud":
                    sts_aud,
            },
        )

        ebs_csi_role = iam.Role(
            self,
            "EbsCsiDriverRole",
            assumed_by=iam.FederatedPrincipal(
                oidc_provider.open_id_connect_provider_arn,
                conditions={"StringEquals": conditions},
                assume_role_action="sts:AssumeRoleWithWebIdentity",
            ),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonEBSCSIDriverPolicy"
                )
            ],
        )

        # Add EBS CSI Driver addon — SA is auto-created by the addon, no need to create manually
        ebs_csi_addon = eks.CfnAddon(
            self,
            "EbsCsiDriverAddon",
            addon_name="aws-ebs-csi-driver",
            cluster_name=self.cluster.cluster_name,
            service_account_role_arn=ebs_csi_role.role_arn,
            resolve_conflicts="OVERWRITE",
        )

        # CoreDNS addon
        eks.CfnAddon(
            self,
            "CoreDnsAddon",
            addon_name="coredns",
            cluster_name=self.cluster.cluster_name,
            resolve_conflicts="OVERWRITE",
        )

        # kube-proxy addon
        eks.CfnAddon(
            self,
            "KubeProxyAddon",
            addon_name="kube-proxy",
            cluster_name=self.cluster.cluster_name,
            resolve_conflicts="OVERWRITE",
        )

        # gp3 StorageClass (encrypted, default)
        gp3_storage_class = self.cluster.add_manifest(
            "Gp3StorageClass",
            {
                "apiVersion": "storage.k8s.io/v1",
                "kind": "StorageClass",
                "metadata": {
                    "name": "gp3",
                    "annotations": {
                        "storageclass.kubernetes.io/is-default-class": "true",
                    },
                },
                "provisioner": "ebs.csi.aws.com",
                "volumeBindingMode": "WaitForFirstConsumer",
                "parameters": {
                    "type": "gp3",
                    "encrypted": "true",
                },
            },
        )
        gp3_storage_class.node.add_dependency(ebs_csi_addon)

        # ─── AWS Load Balancer Controller ───────────────────────────────────
        # IRSA for the controller
        lb_controller_conditions = cdk.CfnJson(
            self, "LbControllerCondition",
            value={
                f"{oidc_provider.open_id_connect_provider_issuer}:sub":
                    "system:serviceaccount:kube-system:aws-load-balancer-controller",
                f"{oidc_provider.open_id_connect_provider_issuer}:aud":
                    sts_aud,
            },
        )

        lb_controller_role = iam.Role(
            self,
            "LbControllerRole",
            assumed_by=iam.FederatedPrincipal(
                oidc_provider.open_id_connect_provider_arn,
                conditions={"StringEquals": lb_controller_conditions},
                assume_role_action="sts:AssumeRoleWithWebIdentity",
            ),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "ElasticLoadBalancingFullAccess"
                )
            ],
        )
        # Additional permissions needed by LB Controller
        lb_controller_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "ec2:DescribeAccountAttributes",
                "ec2:DescribeAddresses",
                "ec2:DescribeAvailabilityZones",
                "ec2:DescribeInternetGateways",
                "ec2:DescribeVpcs",
                "ec2:DescribeVpcPeeringConnections",
                "ec2:DescribeSubnets",
                "ec2:DescribeSecurityGroups",
                "ec2:DescribeInstances",
                "ec2:DescribeNetworkInterfaces",
                "ec2:DescribeTags",
                "ec2:GetCoipPoolUsage",
                "ec2:DescribeCoipPools",
                "ec2:AuthorizeSecurityGroupIngress",
                "ec2:RevokeSecurityGroupIngress",
                "ec2:CreateSecurityGroup",
                "ec2:CreateTags",
                "ec2:DeleteTags",
                "ec2:DeleteSecurityGroup",
                "ec2:ModifyNetworkInterfaceAttribute",
            ],
            resources=["*"],
        ))

        lb_controller_chart = self.cluster.add_helm_chart(
            "AwsLoadBalancerController",
            chart="aws-load-balancer-controller",
            repository="https://aws.github.io/eks-charts",
            namespace="kube-system",
            release="aws-load-balancer-controller",
            values={
                "clusterName": self.cluster.cluster_name,
                "serviceAccount": {
                    "create": True,
                    "name": "aws-load-balancer-controller",
                    "annotations": {
                        "eks.amazonaws.com/role-arn": lb_controller_role.role_arn,
                    },
                },
                "region": self.region,
                "vpcId": vpc.vpc_id,
            },
        )
        lb_controller_chart.node.add_dependency(self.nodegroup)

        # openclaw-operator namespace
        self.cluster.add_manifest(
            "OperatorNamespace",
            {
                "apiVersion": "v1",
                "kind": "Namespace",
                "metadata": {
                    "name": "openclaw-operator-system",
                },
            },
        )

        # NOTE: openclaw-operator Helm chart is installed post-deploy via:
        #   helm install openclaw-operator \
        #     oci://ghcr.io/openclaw-rocks/charts/openclaw-operator \
        #     --namespace openclaw-operator-system \
        #     --set leaderElection.enabled=true \
        #     --set crds.install=true
        # CDK's add_helm_chart doesn't support non-ECR OCI registries (ghcr.io).

        # ——— Outputs ———

        cdk.CfnOutput(self, "ClusterName",
            value=self.cluster.cluster_name,
            description="EKS Cluster Name")

        cdk.CfnOutput(self, "ClusterArn",
            value=self.cluster.cluster_arn,
            description="EKS Cluster ARN")

        cdk.CfnOutput(self, "KubectlRoleArn",
            value=self.cluster.kubectl_role.role_arn if self.cluster.kubectl_role else "N/A",
            description="kubectl Role ARN")

        cdk.CfnOutput(self, "ConfigCommand",
            value=f"aws eks update-kubeconfig --name {self.cluster.cluster_name} --region {self.region}",
            description="kubectl config command")
