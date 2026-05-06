"""EKS stack for OpenClaw SaaS — L1 constructs only, no Lambda.

Mirrors CloudFormation template: CfnCluster + CfnNodegroup + CfnAddon +
CfnAccessEntry + OIDC Provider.  The deploying IAM identity (CDK caller)
is automatically granted cluster-admin via an Access Entry.
"""
import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_eks as eks
from aws_cdk import aws_iam as iam
from constructs import Construct


class EksStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        config,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cluster_name = config.cluster_name
        partition = self.partition  # aws-cn in China

        # =====================================================================
        # Cluster IAM Role
        # =====================================================================
        self.cluster_role = iam.Role(
            self,
            "ClusterRole",
            role_name=f"{cluster_name}-cluster-role",
            assumed_by=iam.ServicePrincipal("eks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEKSClusterPolicy"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEKSVPCResourceController"),
            ],
        )

        # =====================================================================
        # Cluster Security Group (additional SG, tagged for Karpenter)
        # =====================================================================
        self.cluster_security_group = ec2.SecurityGroup(
            self,
            "ClusterSG",
            vpc=vpc,
            description=f"EKS cluster security group for {cluster_name}",
            allow_all_outbound=True,
        )
        cdk.Tags.of(self.cluster_security_group).add("karpenter.sh/discovery", cluster_name)

        # Allow all traffic from VPC CIDR to cluster-managed SG (added post-create below)

        # =====================================================================
        # EKS Cluster (L1 — no Lambda)
        # =====================================================================
        all_subnet_ids = [s.subnet_id for s in vpc.public_subnets + vpc.private_subnets]

        self.cluster = eks.CfnCluster(
            self,
            "EKSCluster",
            name=cluster_name,
            version=config.eks_version,
            role_arn=self.cluster_role.role_arn,
            resources_vpc_config=eks.CfnCluster.ResourcesVpcConfigProperty(
                subnet_ids=all_subnet_ids,
                security_group_ids=[self.cluster_security_group.security_group_id],
                endpoint_public_access=True,
                endpoint_private_access=True,
            ),
            access_config=eks.CfnCluster.AccessConfigProperty(
                authentication_mode="API_AND_CONFIG_MAP",
            ),
            logging=eks.CfnCluster.LoggingProperty(
                cluster_logging=eks.CfnCluster.ClusterLoggingProperty(
                    enabled_types=[
                        eks.CfnCluster.LoggingTypeConfigProperty(type="api"),
                        eks.CfnCluster.LoggingTypeConfigProperty(type="audit"),
                        eks.CfnCluster.LoggingTypeConfigProperty(type="authenticator"),
                    ]
                )
            ),
            tags=[
                cdk.CfnTag(key="Environment", value="production"),
                cdk.CfnTag(key="Project", value="openclaw-multi-tenant"),
            ],
        )

        # Allow VPC CIDR into the EKS-managed cluster SG (applied to nodes)
        ec2.CfnSecurityGroupIngress(
            self,
            "ClusterManagedSGIngressFromVPC",
            group_id=self.cluster.attr_cluster_security_group_id,
            ip_protocol="-1",
            cidr_ip=config.vpc_cidr,
            description="Allow all traffic from VPC CIDR",
        )

        # =====================================================================
        # OIDC Provider
        # =====================================================================
        oidc_issuer_url = self.cluster.attr_open_id_connect_issuer_url
        # Strip https:// to get the host for IAM
        oidc_host = cdk.Fn.select(1, cdk.Fn.split("//", oidc_issuer_url))

        self.oidc_provider = iam.CfnOIDCProvider(
            self,
            "OIDCProvider",
            url=oidc_issuer_url,
            client_id_list=["sts.amazonaws.com"],
            thumbprint_list=[config.oidc_thumbprint],
            tags=[cdk.CfnTag(key="Name", value=f"{cluster_name}-oidc")],
        )
        self.oidc_provider.add_dependency(self.cluster)

        # Store for cross-stack references
        self.oidc_host = oidc_host
        self.oidc_provider_arn = cdk.Fn.sub(
            "arn:${AWS::Partition}:iam::${AWS::AccountId}:oidc-provider/${OidcHost}",
            {"OidcHost": oidc_host},
        )

        # =====================================================================
        # Node Group Role
        # =====================================================================
        self.node_role = iam.Role(
            self,
            "NodeGroupRole",
            role_name=f"{cluster_name}-nodegroup-role",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEKSWorkerNodePolicy"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEKS_CNI_Policy"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEC2ContainerRegistryReadOnly"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
            ],
        )
        # SQS permission added later by IAM stack

        # =====================================================================
        # Launch Template (gp3 encrypted, IMDSv2)
        # =====================================================================
        launch_template = ec2.CfnLaunchTemplate(
            self,
            "NodeLaunchTemplate",
            launch_template_name=f"{cluster_name}-system-nodes-lt",
            launch_template_data=ec2.CfnLaunchTemplate.LaunchTemplateDataProperty(
                block_device_mappings=[
                    ec2.CfnLaunchTemplate.BlockDeviceMappingProperty(
                        device_name="/dev/xvda",
                        ebs=ec2.CfnLaunchTemplate.EbsProperty(
                            volume_size=config.eks_node_volume_size,
                            volume_type="gp3",
                            encrypted=True,
                            delete_on_termination=True,
                        ),
                    )
                ],
                metadata_options=ec2.CfnLaunchTemplate.MetadataOptionsProperty(
                    http_endpoint="enabled",
                    http_put_response_hop_limit=2,
                    http_tokens="required",
                ),
                tag_specifications=[
                    ec2.CfnLaunchTemplate.TagSpecificationProperty(
                        resource_type="instance",
                        tags=[
                            cdk.CfnTag(key="Name", value=f"{cluster_name}-system-node"),
                            cdk.CfnTag(key="Environment", value="production"),
                        ],
                    )
                ],
            ),
        )

        # =====================================================================
        # Managed Node Group
        # =====================================================================
        private_subnet_ids = [s.subnet_id for s in vpc.private_subnets]

        self.nodegroup = eks.CfnNodegroup(
            self,
            "SystemNodeGroup",
            cluster_name=cluster_name,
            nodegroup_name="standard-nodes",
            node_role=self.node_role.role_arn,
            subnets=private_subnet_ids,
            instance_types=[config.eks_node_instance_type],
            ami_type=config.eks_node_ami_type,
            scaling_config=eks.CfnNodegroup.ScalingConfigProperty(
                desired_size=config.eks_node_desired,
                min_size=config.eks_node_min,
                max_size=config.eks_node_max,
            ),
            launch_template=eks.CfnNodegroup.LaunchTemplateSpecificationProperty(
                id=launch_template.ref,
                version=launch_template.attr_latest_version_number,
            ),
            labels={
                "workload-type": "standard",
                "node-class": "system",
            },
            tags={"Name": f"{cluster_name}-standard-node"},
        )
        self.nodegroup.add_dependency(self.cluster)

        # =====================================================================
        # EKS Access Entries
        # =====================================================================

        # Node group access
        eks.CfnAccessEntry(
            self,
            "NodeGroupAccessEntry",
            cluster_name=cluster_name,
            principal_arn=self.node_role.role_arn,
            type="EC2_LINUX",
        ).add_dependency(self.cluster)

        # CDK deployer access — grant cluster-admin to the identity running CDK
        # This eliminates the need for manual role assumption / trust policy editing
        deployer_role_arn = config.app.node.try_get_context("deployer_role_arn")
        if deployer_role_arn:
            deployer_entry = eks.CfnAccessEntry(
                self,
                "DeployerAccessEntry",
                cluster_name=cluster_name,
                principal_arn=deployer_role_arn,
                type="STANDARD",
                access_policies=[
                    eks.CfnAccessEntry.AccessPolicyProperty(
                        policy_arn=f"arn:{partition}:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy",
                        access_scope=eks.CfnAccessEntry.AccessScopeProperty(type="cluster"),
                    )
                ],
            )
            deployer_entry.add_dependency(self.cluster)

        # =====================================================================
        # EKS Add-ons (depend on node group being ready)
        # =====================================================================
        for addon_name in ["vpc-cni", "coredns", "kube-proxy"]:
            addon = eks.CfnAddon(
                self,
                f"{addon_name.replace('-', '')}Addon",
                cluster_name=cluster_name,
                addon_name=addon_name,
                resolve_conflicts="OVERWRITE",
            )
            addon.add_dependency(self.nodegroup)

        # EBS CSI Driver — uses Pod Identity
        ebs_csi_role = iam.Role(
            self,
            "EbsCsiDriverRole",
            role_name=f"{cluster_name}-ebs-csi-role",
            assumed_by=iam.ServicePrincipal(
                "pods.eks.amazonaws.com",
                conditions={"StringEquals": {"aws:SourceAccount": self.account}},
            ),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonEBSCSIDriverPolicy"
                )
            ],
        )
        # Pod Identity requires sts:TagSession
        ebs_csi_role.assume_role_policy.add_statements(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("pods.eks.amazonaws.com")],
                actions=["sts:TagSession"],
            )
        )

        ebs_addon = eks.CfnAddon(
            self,
            "EbsCsiAddon",
            cluster_name=cluster_name,
            addon_name="aws-ebs-csi-driver",
            resolve_conflicts="OVERWRITE",
            pod_identity_associations=[
                eks.CfnAddon.PodIdentityAssociationProperty(
                    role_arn=ebs_csi_role.role_arn,
                    service_account="ebs-csi-controller-sa",
                )
            ],
        )
        ebs_addon.add_dependency(self.nodegroup)

        # Pod Identity Agent addon
        pod_id_addon = eks.CfnAddon(
            self,
            "PodIdentityAddon",
            cluster_name=cluster_name,
            addon_name="eks-pod-identity-agent",
            resolve_conflicts="OVERWRITE",
        )
        pod_id_addon.add_dependency(self.nodegroup)

        # =====================================================================
        # Platform NLB Security Group (pre-created, stable across redeploys)
        # =====================================================================
        self.platform_nlb_sg = ec2.SecurityGroup(
            self,
            "PlatformNLBSecurityGroup",
            vpc=vpc,
            description=f"{cluster_name} Platform API NLB - pre-created for stable SG across redeploys",
            allow_all_outbound=True,
        )
        self.platform_nlb_sg.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(8890),
            description="Platform API HTTP from anywhere",
        )
        cdk.Tags.of(self.platform_nlb_sg).add("Name", f"{cluster_name}-platform-nlb-sg")

        # =====================================================================
        # Outputs
        # =====================================================================
        cdk.CfnOutput(self, "ClusterName", value=cluster_name, description="EKS Cluster Name")
        cdk.CfnOutput(
            self,
            "ClusterEndpoint",
            value=self.cluster.attr_endpoint,
            description="EKS Cluster API Endpoint",
        )
        cdk.CfnOutput(
            self,
            "ClusterOIDCIssuer",
            value=oidc_issuer_url,
            description="OIDC issuer URL",
        )
        cdk.CfnOutput(
            self,
            "ClusterSecurityGroupId",
            value=self.cluster_security_group.security_group_id,
            description="Cluster additional security group ID",
        )
        cdk.CfnOutput(
            self,
            "NodeGroupRoleArn",
            value=self.node_role.role_arn,
            description="Node group IAM role ARN",
        )
        cdk.CfnOutput(
            self,
            "PlatformNLBSecurityGroupId",
            value=self.platform_nlb_sg.security_group_id,
            description="Pre-created Security Group for Platform NLB",
        )
        cdk.CfnOutput(
            self,
            "ConfigCommand",
            value=f"aws eks update-kubeconfig --name {cluster_name} --region {self.region}",
            description="kubectl config command (no --role-arn needed)",
        )
