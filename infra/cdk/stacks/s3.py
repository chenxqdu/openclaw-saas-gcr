"""S3 stack for OpenClaw SaaS — mirrors CloudFormation template.

Includes: Backup Bucket + Backup IAM Role (Pod Identity).
"""
import aws_cdk as cdk
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_iam as iam
from constructs import Construct


class S3Stack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cluster_name = config.cluster_name

        # Backup bucket (matches CF: openclaw-backups-${AccountId})
        self.backups_bucket = s3.Bucket(
            self,
            "BackupsBucket",
            bucket_name=f"openclaw-backups-{self.account}",
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

        # Backup Role (Pod Identity, matches CF)
        self.backup_role = iam.Role(
            self,
            "BackupRole",
            role_name=f"{cluster_name}-openclaw-backup-role",
            assumed_by=iam.ServicePrincipal("pods.eks.amazonaws.com"),
        )
        self.backup_role.assume_role_policy.add_statements(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                principals=[iam.ServicePrincipal("pods.eks.amazonaws.com")],
                actions=["sts:TagSession"],
            )
        )
        self.backup_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["s3:PutObject", "s3:GetObject", "s3:ListBucket", "s3:DeleteObject"],
                resources=[
                    self.backups_bucket.bucket_arn,
                    f"{self.backups_bucket.bucket_arn}/*",
                ],
            )
        )

        # Outputs
        cdk.CfnOutput(self, "BackupBucketName", value=self.backups_bucket.bucket_name)
        cdk.CfnOutput(self, "BackupBucketArn", value=self.backups_bucket.bucket_arn)
        cdk.CfnOutput(self, "BackupRoleArn", value=self.backup_role.role_arn)
