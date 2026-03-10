"""IAM stack for OpenClaw SaaS (China region)"""
import aws_cdk as cdk
from aws_cdk import aws_iam as iam
from aws_cdk import aws_eks as eks
from constructs import Construct


def sts_audience(partition: str) -> str:
    """Return STS audience for OIDC trust — .cn suffix for China partition."""
    if partition == "aws-cn":
        return "sts.amazonaws.com.cn"
    return "sts.amazonaws.com"


class IamStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        cluster: eks.Cluster,
        usage_queue_arn: str,
        config,
        node_role: iam.IRole = None,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        partition = self.partition  # "aws-cn" in China, "aws" in global
        sts_aud = sts_audience(partition)

        # IRSA role for platform-api service account
        oidc_provider = cluster.open_id_connect_provider

        conditions = cdk.CfnJson(
            self,
            "PlatformApiCondition",
            value={
                f"{oidc_provider.open_id_connect_provider_issuer}:sub":
                    "system:serviceaccount:openclaw-platform:platform-api",
                f"{oidc_provider.open_id_connect_provider_issuer}:aud":
                    sts_aud,
            },
        )

        self.platform_api_role = iam.Role(
            self,
            "PlatformApiRole",
            role_name=f"{config.resource_prefix}-platform-api-role",
            assumed_by=iam.FederatedPrincipal(
                oidc_provider.open_id_connect_provider_arn,
                conditions={"StringEquals": conditions},
                assume_role_action="sts:AssumeRoleWithWebIdentity",
            ),
            description="IRSA role for OpenClaw platform API",
        )

        # SQS permissions for usage events
        self.platform_api_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "sqs:SendMessage",
                    "sqs:GetQueueUrl",
                    "sqs:GetQueueAttributes",
                ],
                resources=[usage_queue_arn],
            )
        )

        # Kubernetes API access (for managing tenant resources)
        # Note: K8s RBAC is defined separately in k8s/platform/rbac.yaml
        # This is just for pod logs access
        self.platform_api_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "logs:GetLogEvents",
                    "logs:FilterLogEvents",
                ],
                resources=[
                    f"arn:{partition}:logs:{self.region}:{self.account}:log-group:/aws/eks/{cluster.cluster_name}/cluster:*",
                    f"arn:{partition}:logs:{self.region}:{self.account}:log-group:/aws/containerinsights/{cluster.cluster_name}/*:*",
                ],
            )
        )

        # NOTE: Bedrock is NOT available in AWS China regions.
        # No node Bedrock policy needed. Tenants must use external LLM providers
        # (OpenAI, Anthropic) with their own API keys.

        # ——— Node Role SQS permissions ———
        # OpenClaw agent pods run on nodes (not via IRSA), so the node instance
        # role also needs SQS access for the metrics-exporter sidecar to push
        # usage events.
        if node_role is not None:
            node_role.add_to_principal_policy(
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=[
                        "sqs:SendMessage",
                        "sqs:SendMessageBatch",
                        "sqs:GetQueueUrl",
                        "sqs:GetQueueAttributes",
                    ],
                    resources=[usage_queue_arn],
                )
            )

        # Outputs
        cdk.CfnOutput(
            self,
            "PlatformApiRoleArn",
            value=self.platform_api_role.role_arn,
            description="ARN of the platform API IRSA role",
            export_name=f"{config.stack_prefix}-platform-api-role-arn",
        )
