"""IAM stack for OpenClaw SaaS — mirrors CloudFormation template.

Includes: ALB Controller (IRSA), Karpenter (IRSA + Node Role),
EFS CSI Driver (Pod Identity), Platform API (Pod Identity),
Backup Role (Pod Identity), Node Role SQS + ALB permissions.
"""
import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_sqs as sqs
from constructs import Construct


class IamStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config,
        # Cross-stack references
        oidc_host,           # e.g. oidc.eks.cn-northwest-1.amazonaws.com.cn/id/XXXX
        oidc_provider_arn,   # arn:aws-cn:iam::...:oidc-provider/...
        cluster_name: str,
        node_role: iam.IRole,
        usage_queue: sqs.IQueue,
        usage_dlq: sqs.IQueue,
        karpenter_queue: sqs.IQueue,
        karpenter_node_role: iam.IRole,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        partition = self.partition

        # =================================================================
        # Node Role: SQS permissions for metrics-exporter sidecar
        # =================================================================
        node_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="MetricsExporterSQS",
                effect=iam.Effect.ALLOW,
                actions=["sqs:SendMessage"],
                resources=[usage_queue.queue_arn],
            )
        )

        # =================================================================
        # Node Role: ALB Controller permissions (runs on nodes)
        # =================================================================
        node_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("ElasticLoadBalancingFullAccess")
        )
        node_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="AlbControllerEc2",
                effect=iam.Effect.ALLOW,
                actions=[
                    "ec2:CreateSecurityGroup", "ec2:DeleteSecurityGroup",
                    "ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress",
                    "ec2:CreateTags", "ec2:DeleteTags",
                    "ec2:DescribeSecurityGroups", "ec2:DescribeInstances",
                    "ec2:DescribeSubnets", "ec2:DescribeVpcs",
                    "ec2:DescribeAvailabilityZones", "ec2:DescribeAccountAttributes",
                    "ec2:DescribeInternetGateways", "ec2:DescribeNetworkInterfaces",
                    "ec2:DescribeTargetGroups", "ec2:DescribeTargetHealth",
                    "ec2:ModifyNetworkInterfaceAttribute",
                ],
                resources=["*"],
            )
        )
        node_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="AlbControllerShield",
                effect=iam.Effect.ALLOW,
                actions=[
                    "shield:GetSubscriptionState", "shield:DescribeProtection",
                    "shield:CreateProtection", "shield:DeleteProtection",
                ],
                resources=["*"],
            )
        )
        node_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="AlbControllerWaf",
                effect=iam.Effect.ALLOW,
                actions=[
                    "waf-regional:GetWebACLForResource", "waf-regional:GetWebACL",
                    "waf-regional:AssociateWebACL", "waf-regional:DisassociateWebACL",
                    "wafv2:GetWebACL", "wafv2:GetWebACLForResource",
                    "wafv2:AssociateWebACL", "wafv2:DisassociateWebACL",
                ],
                resources=["*"],
            )
        )
        node_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="AlbControllerSlr",
                effect=iam.Effect.ALLOW,
                actions=["iam:CreateServiceLinkedRole"],
                resources=["*"],
                conditions={
                    "StringEquals": {"iam:AWSServiceName": "elasticloadbalancing.amazonaws.com"}
                },
            )
        )

        # =================================================================
        # ALB Controller IRSA Role (fine-grained, matches CF template)
        # =================================================================
        alb_conditions = cdk.CfnJson(
            self, "AlbCondition",
            value={
                f"{oidc_host}:aud": "sts.amazonaws.com",
                f"{oidc_host}:sub": "system:serviceaccount:kube-system:aws-load-balancer-controller",
            },
        )

        alb_policy = iam.ManagedPolicy(
            self,
            "ALBControllerPolicy",
            managed_policy_name=f"{cluster_name}-ALBControllerPolicy",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["iam:CreateServiceLinkedRole"],
                    resources=["*"],
                    conditions={"StringEquals": {"iam:AWSServiceName": "elasticloadbalancing.amazonaws.com"}},
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "ec2:DescribeAccountAttributes", "ec2:DescribeAddresses",
                        "ec2:DescribeAvailabilityZones", "ec2:DescribeInternetGateways",
                        "ec2:DescribeVpcs", "ec2:DescribeVpcPeeringConnections",
                        "ec2:DescribeSubnets", "ec2:DescribeSecurityGroups",
                        "ec2:DescribeInstances", "ec2:DescribeNetworkInterfaces",
                        "ec2:DescribeTags", "ec2:DescribeCoipPools",
                        "ec2:GetCoipPoolUsage", "ec2:DescribeVpcEndpoints",
                        "elasticloadbalancing:DescribeLoadBalancers",
                        "elasticloadbalancing:DescribeLoadBalancerAttributes",
                        "elasticloadbalancing:DescribeListeners",
                        "elasticloadbalancing:DescribeListenerAttributes",
                        "elasticloadbalancing:DescribeListenerCertificates",
                        "elasticloadbalancing:DescribeSSLPolicies",
                        "elasticloadbalancing:DescribeRules",
                        "elasticloadbalancing:DescribeTargetGroups",
                        "elasticloadbalancing:DescribeTargetGroupAttributes",
                        "elasticloadbalancing:DescribeTargetHealth",
                        "elasticloadbalancing:DescribeTags",
                        "elasticloadbalancing:DescribeTrustStores",
                    ],
                    resources=["*"],
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "acm:ListCertificates", "acm:DescribeCertificate",
                        "iam:ListServerCertificates", "iam:GetServerCertificate",
                        "waf-regional:GetWebACL", "waf-regional:GetWebACLForResource",
                        "waf-regional:AssociateWebACL", "waf-regional:DisassociateWebACL",
                        "wafv2:GetWebACL", "wafv2:GetWebACLForResource",
                        "wafv2:AssociateWebACL", "wafv2:DisassociateWebACL",
                    ],
                    resources=["*"],
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress",
                        "ec2:CreateSecurityGroup",
                    ],
                    resources=["*"],
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "elasticloadbalancing:CreateLoadBalancer",
                        "elasticloadbalancing:CreateTargetGroup",
                    ],
                    resources=["*"],
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "elasticloadbalancing:CreateListener", "elasticloadbalancing:DeleteListener",
                        "elasticloadbalancing:CreateRule", "elasticloadbalancing:DeleteRule",
                    ],
                    resources=["*"],
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "elasticloadbalancing:RegisterTargets",
                        "elasticloadbalancing:DeregisterTargets",
                    ],
                    resources=[f"arn:{partition}:elasticloadbalancing:*:*:targetgroup/*/*"],
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "elasticloadbalancing:SetWebAcl",
                        "elasticloadbalancing:ModifyListener",
                        "elasticloadbalancing:AddListenerCertificates",
                        "elasticloadbalancing:RemoveListenerCertificates",
                        "elasticloadbalancing:ModifyRule",
                        "elasticloadbalancing:ModifyLoadBalancerAttributes",
                        "elasticloadbalancing:SetIpAddressType",
                        "elasticloadbalancing:SetSecurityGroups",
                        "elasticloadbalancing:SetSubnets",
                        "elasticloadbalancing:DeleteLoadBalancer",
                        "elasticloadbalancing:ModifyTargetGroup",
                        "elasticloadbalancing:ModifyTargetGroupAttributes",
                        "elasticloadbalancing:DeleteTargetGroup",
                        "elasticloadbalancing:AddTags",
                        "elasticloadbalancing:RemoveTags",
                    ],
                    resources=["*"],
                ),
            ],
        )

        self.alb_controller_role = iam.Role(
            self,
            "ALBControllerRole",
            role_name=f"{cluster_name}-ALBControllerRole",
            assumed_by=iam.FederatedPrincipal(
                oidc_provider_arn,
                conditions={"StringEquals": alb_conditions},
                assume_role_action="sts:AssumeRoleWithWebIdentity",
            ),
            managed_policies=[alb_policy],
        )

        # =================================================================
        # EFS CSI Driver Role (Pod Identity)
        # =================================================================
        efs_policy = iam.ManagedPolicy(
            self,
            "EFSCSIDriverPolicy",
            managed_policy_name=f"{cluster_name}-EFS-CSI-Driver-Policy",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "elasticfilesystem:DescribeAccessPoints",
                        "elasticfilesystem:DescribeFileSystems",
                        "elasticfilesystem:DescribeMountTargets",
                        "elasticfilesystem:TagResource",
                        "elasticfilesystem:UntagResource",
                    ],
                    resources=["*"],
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["elasticfilesystem:CreateAccessPoint"],
                    resources=["*"],
                    conditions={"StringLike": {"aws:RequestTag/efs.csi.aws.com/cluster": "true"}},
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["elasticfilesystem:DeleteAccessPoint"],
                    resources=["*"],
                    conditions={"StringEquals": {"aws:ResourceTag/efs.csi.aws.com/cluster": "true"}},
                ),
            ],
        )

        self.efs_csi_role = iam.Role(
            self,
            "EFSCSIDriverRole",
            role_name=f"{cluster_name}-EFS-CSI-DriverRole",
            assumed_by=iam.ServicePrincipal("pods.eks.amazonaws.com"),
            managed_policies=[efs_policy],
        )
        self.efs_csi_role.assume_role_policy.add_statements(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("pods.eks.amazonaws.com")],
                actions=["sts:TagSession"],
            )
        )

        # =================================================================
        # Platform API Role (Pod Identity for SQS)
        # =================================================================
        self.platform_api_role = iam.Role(
            self,
            "PlatformApiRole",
            role_name=f"{cluster_name}-platform-api",
            assumed_by=iam.ServicePrincipal("pods.eks.amazonaws.com"),
        )
        self.platform_api_role.assume_role_policy.add_statements(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("pods.eks.amazonaws.com")],
                actions=["sts:TagSession"],
            )
        )
        self.platform_api_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage",
                    "sqs:GetQueueUrl", "sqs:GetQueueAttributes",
                ],
                resources=[usage_queue.queue_arn, usage_dlq.queue_arn],
            )
        )

        # =================================================================
        # Karpenter Node Role (SQS for metrics-exporter)
        # =================================================================
        karpenter_node_role.add_to_principal_policy(
            iam.PolicyStatement(
                sid="MetricsExporterSQS",
                effect=iam.Effect.ALLOW,
                actions=["sqs:SendMessage"],
                resources=[usage_queue.queue_arn],
            )
        )

        # =================================================================
        # Karpenter Controller Policy (matches CF template)
        # =================================================================
        karpenter_controller_policy = iam.ManagedPolicy(
            self,
            "KarpenterControllerPolicy",
            managed_policy_name=f"KarpenterControllerPolicy-{cluster_name}",
            statements=[
                iam.PolicyStatement(
                    sid="AllowScopedEC2InstanceAccessActions",
                    effect=iam.Effect.ALLOW,
                    actions=["ec2:RunInstances", "ec2:CreateFleet"],
                    resources=[
                        f"arn:{partition}:ec2:{self.region}::image/*",
                        f"arn:{partition}:ec2:{self.region}::snapshot/*",
                        f"arn:{partition}:ec2:{self.region}:*:security-group/*",
                        f"arn:{partition}:ec2:{self.region}:*:subnet/*",
                    ],
                ),
                iam.PolicyStatement(
                    sid="AllowRegionalReadActions",
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "ec2:DescribeAvailabilityZones", "ec2:DescribeImages",
                        "ec2:DescribeInstances", "ec2:DescribeInstanceTypeOfferings",
                        "ec2:DescribeInstanceTypes", "ec2:DescribeLaunchTemplates",
                        "ec2:DescribeSecurityGroups", "ec2:DescribeSpotPriceHistory",
                        "ec2:DescribeSubnets",
                    ],
                    resources=["*"],
                    conditions={"StringEquals": {"aws:RequestedRegion": self.region}},
                ),
                iam.PolicyStatement(
                    sid="AllowSSMReadActions",
                    effect=iam.Effect.ALLOW,
                    actions=["ssm:GetParameter"],
                    resources=[f"arn:{partition}:ssm:{self.region}::parameter/aws/service/*"],
                ),
                iam.PolicyStatement(
                    sid="AllowPricingReadActions",
                    effect=iam.Effect.ALLOW,
                    actions=["pricing:GetProducts"],
                    resources=["*"],
                ),
                iam.PolicyStatement(
                    sid="AllowSQSActions",
                    effect=iam.Effect.ALLOW,
                    actions=["sqs:DeleteMessage", "sqs:GetQueueUrl", "sqs:ReceiveMessage"],
                    resources=[karpenter_queue.queue_arn],
                ),
                iam.PolicyStatement(
                    sid="AllowPassingInstanceRole",
                    effect=iam.Effect.ALLOW,
                    actions=["iam:PassRole"],
                    resources=[karpenter_node_role.role_arn],
                    conditions={"StringEquals": {"iam:PassedToService": "ec2.amazonaws.com"}},
                ),
                iam.PolicyStatement(
                    sid="AllowInstanceProfileReadActions",
                    effect=iam.Effect.ALLOW,
                    actions=["iam:GetInstanceProfile"],
                    resources=["*"],
                ),
                iam.PolicyStatement(
                    sid="AllowAPIServerEndpointDiscovery",
                    effect=iam.Effect.ALLOW,
                    actions=["eks:DescribeCluster"],
                    resources=[f"arn:{partition}:eks:{self.region}:{self.account}:cluster/{cluster_name}"],
                ),
                # Scoped EC2 actions with tag conditions
                iam.PolicyStatement(
                    sid="AllowScopedEC2InstanceActionsWithTags",
                    effect=iam.Effect.ALLOW,
                    actions=["ec2:RunInstances", "ec2:CreateFleet", "ec2:CreateLaunchTemplate"],
                    resources=[
                        f"arn:{partition}:ec2:{self.region}:*:fleet/*",
                        f"arn:{partition}:ec2:{self.region}:*:instance/*",
                        f"arn:{partition}:ec2:{self.region}:*:volume/*",
                        f"arn:{partition}:ec2:{self.region}:*:network-interface/*",
                        f"arn:{partition}:ec2:{self.region}:*:launch-template/*",
                        f"arn:{partition}:ec2:{self.region}:*:spot-instances-request/*",
                    ],
                    conditions={
                        "StringEquals": {f"aws:RequestTag/kubernetes.io/cluster/{cluster_name}": "owned"},
                        "StringLike": {"aws:RequestTag/karpenter.sh/nodepool": "*"},
                    },
                ),
                iam.PolicyStatement(
                    sid="AllowScopedDeletion",
                    effect=iam.Effect.ALLOW,
                    actions=["ec2:TerminateInstances", "ec2:DeleteLaunchTemplate"],
                    resources=[
                        f"arn:{partition}:ec2:{self.region}:*:instance/*",
                        f"arn:{partition}:ec2:{self.region}:*:launch-template/*",
                    ],
                    conditions={
                        "StringEquals": {f"aws:ResourceTag/kubernetes.io/cluster/{cluster_name}": "owned"},
                        "StringLike": {"aws:ResourceTag/karpenter.sh/nodepool": "*"},
                    },
                ),
                iam.PolicyStatement(
                    sid="AllowScopedResourceCreationTagging",
                    effect=iam.Effect.ALLOW,
                    actions=["ec2:CreateTags"],
                    resources=[
                        f"arn:{partition}:ec2:{self.region}:*:fleet/*",
                        f"arn:{partition}:ec2:{self.region}:*:instance/*",
                        f"arn:{partition}:ec2:{self.region}:*:volume/*",
                        f"arn:{partition}:ec2:{self.region}:*:network-interface/*",
                        f"arn:{partition}:ec2:{self.region}:*:launch-template/*",
                        f"arn:{partition}:ec2:{self.region}:*:spot-instances-request/*",
                    ],
                    conditions={
                        "StringEquals": {
                            f"aws:RequestTag/kubernetes.io/cluster/{cluster_name}": "owned",
                            "ec2:CreateAction": ["RunInstances", "CreateFleet", "CreateLaunchTemplate"],
                        },
                        "StringLike": {"aws:RequestTag/karpenter.sh/nodepool": "*"},
                    },
                ),
                # Instance profile management
                iam.PolicyStatement(
                    sid="AllowScopedInstanceProfileCreationActions",
                    effect=iam.Effect.ALLOW,
                    actions=["iam:CreateInstanceProfile"],
                    resources=["*"],
                    conditions={
                        "StringEquals": {
                            f"aws:RequestTag/kubernetes.io/cluster/{cluster_name}": "owned",
                            "aws:RequestTag/topology.kubernetes.io/region": self.region,
                        },
                        "StringLike": {"aws:RequestTag/karpenter.k8s.aws/ec2nodeclass": "*"},
                    },
                ),
                iam.PolicyStatement(
                    sid="AllowScopedInstanceProfileActions",
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "iam:AddRoleToInstanceProfile",
                        "iam:RemoveRoleFromInstanceProfile",
                        "iam:DeleteInstanceProfile",
                        "iam:TagInstanceProfile",
                    ],
                    resources=["*"],
                    conditions={
                        "StringEquals": {
                            f"aws:ResourceTag/kubernetes.io/cluster/{cluster_name}": "owned",
                            "aws:ResourceTag/topology.kubernetes.io/region": self.region,
                        },
                        "StringLike": {"aws:ResourceTag/karpenter.k8s.aws/ec2nodeclass": "*"},
                    },
                ),
            ],
        )

        karpenter_conditions = cdk.CfnJson(
            self, "KarpenterCondition",
            value={
                f"{oidc_host}:aud": "sts.amazonaws.com",
                f"{oidc_host}:sub": "system:serviceaccount:kube-system:karpenter",
            },
        )

        self.karpenter_controller_role = iam.Role(
            self,
            "KarpenterControllerRole",
            role_name=f"KarpenterControllerRole-{cluster_name}",
            assumed_by=iam.FederatedPrincipal(
                oidc_provider_arn,
                conditions={"StringEquals": karpenter_conditions},
                assume_role_action="sts:AssumeRoleWithWebIdentity",
            ),
            managed_policies=[karpenter_controller_policy],
        )

        # =================================================================
        # Outputs
        # =================================================================
        cdk.CfnOutput(self, "ALBControllerRoleArn", value=self.alb_controller_role.role_arn)
        cdk.CfnOutput(self, "EFSCSIDriverRoleArn", value=self.efs_csi_role.role_arn)
        cdk.CfnOutput(self, "PlatformApiRoleArn", value=self.platform_api_role.role_arn)
        cdk.CfnOutput(self, "KarpenterControllerRoleArn", value=self.karpenter_controller_role.role_arn)
        cdk.CfnOutput(self, "KarpenterNodeRoleArn", value=karpenter_node_role.role_arn)
