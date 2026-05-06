"""Central configuration for OpenClaw SaaS Infrastructure

All configuration values are read from cdk.json context.
Override via cdk.json or CLI: cdk deploy -c key=value
"""
import aws_cdk as cdk


class Config:
    """Configuration container for OpenClaw SaaS infrastructure"""

    def __init__(self, app: cdk.App):
        self.app = app

        # Project identification
        self.project_name = self._get_context("project_name", "openclaw-saas")
        self.environment = self._get_context("environment", "")

        # EKS configuration
        self.cluster_name = self._get_context("cluster_name", "openclaw-prod")
        self.eks_version = self._get_context("eks_version", "1.31")
        self.eks_node_instance_type = self._get_context("eks_node_instance_type", "m6g.xlarge")
        self.eks_node_ami_type = self._get_context("eks_node_ami_type", "AL2023_ARM_64_STANDARD")
        self.eks_node_min = self._get_context("eks_node_min", 2)
        self.eks_node_max = self._get_context("eks_node_max", 4)
        self.eks_node_desired = self._get_context("eks_node_desired", 2)
        self.eks_node_volume_size = self._get_context("eks_node_volume_size", 100)
        self.oidc_thumbprint = self._get_context(
            "oidc_thumbprint", "9e99a48a9960b14926bb7f3b02e22da2b0ab7280"
        )

        # VPC configuration
        self.vpc_cidr = self._get_context("vpc_cidr", "172.31.0.0/16")
        self.vpc_max_azs = self._get_context("vpc_max_azs", 3)

        # RDS configuration
        self.db_instance_class = self._get_context("db_instance_class", "db.t4g.medium")
        self.db_name = self._get_context("db_name", "openclawsaas")
        self.db_allocated_storage = self._get_context("db_allocated_storage", 50)
        self.db_max_allocated_storage = self._get_context("db_max_allocated_storage", 200)

        # SQS configuration
        self.sqs_visibility_timeout = self._get_context("sqs_visibility_timeout", 300)
        self.sqs_retention_period_days = self._get_context("sqs_retention_period_days", 4)
        self.sqs_max_receive_count = self._get_context("sqs_max_receive_count", 3)

        # S3 configuration
        self.s3_lifecycle_transition_days = self._get_context("s3_lifecycle_transition_days", 30)

    def _get_context(self, key: str, default=None):
        """Get context value with fallback to default"""
        value = self.app.node.try_get_context(key)
        return value if value is not None else default

    @property
    def stack_prefix(self) -> str:
        """Get stack name prefix"""
        if self.environment:
            return f"{self.project_name}-{self.environment}"
        return self.project_name

    @property
    def resource_prefix(self) -> str:
        """Get resource name prefix"""
        if self.environment:
            return f"{self.project_name}-{self.environment}"
        return self.project_name

    def get_tags(self) -> dict:
        """Get common tags for all resources"""
        tags = {
            "Project": "openclaw-multi-tenant",
            "ManagedBy": "CDK",
            "Environment": "production",
        }
        if self.environment:
            tags["Environment"] = self.environment
        return tags
