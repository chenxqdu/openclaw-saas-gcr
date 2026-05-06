"""OpenClaw SaaS CDK Application — L1 constructs, no Lambda.

Mirrors the CloudFormation template architecture:
VPC → EKS (CfnCluster) → EFS → SQS → S3 → IAM → RDS

ECR repositories are created by deploy.sh (not CDK).
"""
import os
import aws_cdk as cdk
from config import Config
from stacks.vpc import VpcStack
from stacks.eks import EksStack
from stacks.efs import EfsStack
from stacks.rds import RdsStack
from stacks.s3 import S3Stack
from stacks.sqs import SqsStack
from stacks.iam import IamStack
from stacks.karpenter import KarpenterNodeStack

app = cdk.App()
config = Config(app)

# ── Resolve AWS account and region ──────────────────────────────────────
_region = (
    os.environ.get("AWS_REGION")
    or os.environ.get("CDK_DEFAULT_REGION")
    or app.node.try_get_context("aws_region")
)
if not _region:
    raise ValueError("AWS region not set. Set AWS_REGION, CDK_DEFAULT_REGION, or aws_region in cdk.json.")

_account = os.environ.get("AWS_ACCOUNT_ID") or os.environ.get("CDK_DEFAULT_ACCOUNT")
if not _account:
    raise ValueError("AWS account not set. Set AWS_ACCOUNT_ID or CDK_DEFAULT_ACCOUNT.")

# Auto-infer partition
_partition = "aws-cn" if _region.startswith("cn-") else "aws"
app.node.set_context("@aws-cdk/core:target-partitions", [_partition])

env = cdk.Environment(account=_account, region=_region)

# Common tags
for key, value in config.get_tags().items():
    cdk.Tags.of(app).add(key, value)

# ── Stacks ──────────────────────────────────────────────────────────────

# 1. VPC (3 AZ, NAT, VPC Endpoints)
vpc_stack = VpcStack(app, f"{config.stack_prefix}-vpc", config=config, env=env)

# 2. Karpenter Node Role (needed before EKS for Access Entry)
karpenter_node_stack = KarpenterNodeStack(
    app, f"{config.stack_prefix}-karpenter-node", config=config, env=env,
)

# 3. SQS (Usage Events + Karpenter Interruption + EventBridge rules)
sqs_stack = SqsStack(app, f"{config.stack_prefix}-sqs", config=config, env=env)

# 4. EKS Cluster (L1 CfnCluster — no Lambda)
eks_stack = EksStack(
    app, f"{config.stack_prefix}-eks",
    vpc=vpc_stack.vpc, config=config, env=env,
)
eks_stack.add_dependency(vpc_stack)

# 5. Karpenter Node Access Entry (after EKS cluster exists)
from aws_cdk import aws_eks as eks_mod
karpenter_access = eks_mod.CfnAccessEntry(
    eks_stack,
    "KarpenterNodeAccessEntry",
    cluster_name=config.cluster_name,
    principal_arn=karpenter_node_stack.node_role.role_arn,
    type="EC2_LINUX",
)
karpenter_access.add_dependency(eks_stack.cluster)
eks_stack.add_dependency(karpenter_node_stack)

# 6. EFS
efs_stack = EfsStack(
    app, f"{config.stack_prefix}-efs",
    vpc=vpc_stack.vpc, config=config, env=env,
)
efs_stack.add_dependency(vpc_stack)

# 7. S3 (Backup Bucket + Backup Role)
s3_stack = S3Stack(app, f"{config.stack_prefix}-s3", config=config, env=env)

# 8. IAM (ALB Controller, Karpenter Controller, EFS CSI, Platform API, node policies)
iam_stack = IamStack(
    app, f"{config.stack_prefix}-iam",
    config=config,
    oidc_host=eks_stack.oidc_host,
    oidc_provider_arn=eks_stack.oidc_provider_arn,
    cluster_name=config.cluster_name,
    node_role=eks_stack.node_role,
    usage_queue=sqs_stack.usage_queue,
    usage_dlq=sqs_stack.usage_dlq,
    karpenter_queue=sqs_stack.karpenter_queue,
    karpenter_node_role=karpenter_node_stack.node_role,
    env=env,
)
iam_stack.add_dependency(eks_stack)
iam_stack.add_dependency(sqs_stack)
iam_stack.add_dependency(karpenter_node_stack)

# 9. RDS (PostgreSQL, VPC-only access)
rds_stack = RdsStack(
    app, f"{config.stack_prefix}-rds",
    vpc=vpc_stack.vpc, config=config, env=env,
)
rds_stack.add_dependency(vpc_stack)

app.synth()
