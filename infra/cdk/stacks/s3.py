"""S3 stack for OpenClaw SaaS"""
import aws_cdk as cdk
from aws_cdk import aws_s3 as s3
from constructs import Construct


class S3Stack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Backups and artifacts bucket
        self.backups_bucket = s3.Bucket(
            self,
            "BackupsBucket",
            bucket_name=f"{config.resource_prefix}-backups-{self.account}-{self.region}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=True,
            removal_policy=cdk.RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="TransitionToIA",
                    enabled=True,
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                            transition_after=cdk.Duration.days(config.s3_lifecycle_transition_days),
                        )
                    ],
                ),
                s3.LifecycleRule(
                    id="ExpireOldVersions",
                    enabled=True,
                    noncurrent_version_expiration=cdk.Duration.days(90),
                ),
            ],
        )

        # Outputs
        cdk.CfnOutput(
            self,
            "BackupsBucketName",
            value=self.backups_bucket.bucket_name,
            description="S3 bucket for backups and artifacts",
            export_name=f"{config.stack_prefix}-backups-bucket",
        )

        cdk.CfnOutput(
            self,
            "BackupsBucketArn",
            value=self.backups_bucket.bucket_arn,
            description="ARN of backups bucket",
            export_name=f"{config.stack_prefix}-backups-bucket-arn",
        )
