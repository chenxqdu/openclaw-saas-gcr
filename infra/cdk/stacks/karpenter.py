"""Karpenter Node Role + Instance Profile — mirrors CloudFormation template.

Separated from iam.py because the EKS stack needs the node role ARN
for the Access Entry, and the IAM stack needs the EKS OIDC provider
for the controller role.
"""
import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_eks as eks
from constructs import Construct


class KarpenterNodeStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cluster_name = config.cluster_name

        # Karpenter Node Role (matches CF: KarpenterNodeRole-${ClusterName})
        self.node_role = iam.Role(
            self,
            "KarpenterNodeRole",
            role_name=f"KarpenterNodeRole-{cluster_name}",
            assumed_by=iam.ServicePrincipal("ec2.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEKSWorkerNodePolicy"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEKS_CNI_Policy"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonEC2ContainerRegistryReadOnly"),
                iam.ManagedPolicy.from_aws_managed_policy_name("AmazonSSMManagedInstanceCore"),
            ],
        )

        # Instance Profile
        self.instance_profile = iam.CfnInstanceProfile(
            self,
            "KarpenterNodeInstanceProfile",
            instance_profile_name=f"KarpenterNodeInstanceProfile-{cluster_name}",
            path="/",
            roles=[self.node_role.role_name],
        )

        # Outputs
        cdk.CfnOutput(self, "KarpenterNodeRoleArn", value=self.node_role.role_arn)
        cdk.CfnOutput(
            self,
            "KarpenterNodeInstanceProfileName",
            value=self.instance_profile.instance_profile_name,
        )
