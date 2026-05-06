"""EFS stack for OpenClaw SaaS — mirrors CloudFormation template.

Shared storage for agent workspaces: encrypted EFS with mount targets
in each private subnet.
"""
import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_efs as efs
from constructs import Construct


class EfsStack(cdk.Stack):
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

        # Security group — allow NFS from VPC CIDR
        self.efs_security_group = ec2.SecurityGroup(
            self,
            "EFSSecurityGroup",
            vpc=vpc,
            description="Allow NFS traffic from VPC for EFS",
            allow_all_outbound=True,
        )
        self.efs_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(config.vpc_cidr),
            connection=ec2.Port.tcp(2049),
            description="NFS from VPC",
        )

        # EFS File System (encrypted, elastic throughput)
        self.file_system = efs.FileSystem(
            self,
            "EFSFileSystem",
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_group=self.efs_security_group,
            encrypted=True,
            throughput_mode=efs.ThroughputMode.ELASTIC,
            performance_mode=efs.PerformanceMode.GENERAL_PURPOSE,
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        cdk.Tags.of(self.file_system).add("Name", f"{cluster_name}-shared-storage")

        # Outputs
        cdk.CfnOutput(
            self,
            "EFSFileSystemId",
            value=self.file_system.file_system_id,
            description="EFS file system ID (use in efs-sc StorageClass)",
        )
