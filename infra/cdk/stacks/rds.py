"""RDS stack for OpenClaw SaaS — mirrors CloudFormation template.

PostgreSQL 16, gp3 encrypted, Secrets Manager password, VPC-only access.
"""
import aws_cdk as cdk
from aws_cdk import aws_rds as rds
from aws_cdk import aws_ec2 as ec2
from constructs import Construct


class RdsStack(cdk.Stack):
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

        # Security group — allow PostgreSQL from entire VPC CIDR (matches CF)
        self.db_security_group = ec2.SecurityGroup(
            self,
            "RDSSecurityGroup",
            vpc=vpc,
            description=f"{cluster_name} RDS PostgreSQL access from VPC",
            allow_all_outbound=True,
        )
        self.db_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(config.vpc_cidr),
            connection=ec2.Port.tcp(5432),
            description="PostgreSQL from VPC",
        )

        # Subnet group (private subnets)
        subnet_group = rds.SubnetGroup(
            self,
            "DBSubnetGroup",
            description=f"{cluster_name} private subnets for RDS",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
        )

        # PostgreSQL instance (matches CF: db.t4g.medium, 50-200GB, gp3)
        self.db_instance = rds.DatabaseInstance(
            self,
            "RDSInstance",
            engine=rds.DatabaseInstanceEngine.postgres(
                version=rds.PostgresEngineVersion.VER_16
            ),
            instance_type=ec2.InstanceType(config.db_instance_class),
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[self.db_security_group],
            subnet_group=subnet_group,
            database_name=config.db_name,
            credentials=rds.Credentials.from_generated_secret(
                username="openclaw_admin",
                secret_name=f"{cluster_name}-rds-password",
                exclude_characters="\"@/\\",
            ),
            allocated_storage=config.db_allocated_storage,
            max_allocated_storage=config.db_max_allocated_storage,
            storage_type=rds.StorageType.GP3,
            storage_encrypted=True,
            multi_az=False,
            publicly_accessible=False,
            deletion_protection=False,
            backup_retention=cdk.Duration.days(7),
            preferred_backup_window="03:00-04:00",
            preferred_maintenance_window="sun:04:00-sun:05:00",
            copy_tags_to_snapshot=True,
            removal_policy=cdk.RemovalPolicy.SNAPSHOT,
            auto_minor_version_upgrade=True,
        )

        # Outputs
        cdk.CfnOutput(self, "RDSEndpoint", value=self.db_instance.db_instance_endpoint_address)
        cdk.CfnOutput(self, "RDSPort", value=self.db_instance.db_instance_endpoint_port)
        cdk.CfnOutput(self, "RDSSecretArn", value=self.db_instance.secret.secret_arn)
        cdk.CfnOutput(self, "DbName", value=config.db_name)
