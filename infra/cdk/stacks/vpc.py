"""VPC stack for OpenClaw SaaS — mirrors CloudFormation template.

3 AZ, 6 subnets (3 public + 3 private), single NAT Gateway,
VPC Endpoints for S3/ECR/STS.
"""
import aws_cdk as cdk
from aws_cdk import aws_ec2 as ec2
from constructs import Construct


class VpcStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create VPC: 3 AZ, 1 NAT Gateway (cost-optimized), matching CF template
        self.vpc = ec2.Vpc(
            self,
            "Vpc",
            ip_addresses=ec2.IpAddresses.cidr(config.vpc_cidr),
            max_azs=config.vpc_max_azs,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    map_public_ip_on_launch=True,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                ),
            ],
            enable_dns_hostnames=True,
            enable_dns_support=True,
        )

        # Tag VPC and subnets for EKS / Karpenter discovery
        cluster_name = config.cluster_name
        cdk.Tags.of(self.vpc).add(f"kubernetes.io/cluster/{cluster_name}", "shared")
        cdk.Tags.of(self.vpc).add("karpenter.sh/discovery", cluster_name)

        for subnet in self.vpc.public_subnets:
            cdk.Tags.of(subnet).add("kubernetes.io/role/elb", "1")
            cdk.Tags.of(subnet).add(f"kubernetes.io/cluster/{cluster_name}", "shared")

        for subnet in self.vpc.private_subnets:
            cdk.Tags.of(subnet).add("kubernetes.io/role/internal-elb", "1")
            cdk.Tags.of(subnet).add(f"kubernetes.io/cluster/{cluster_name}", "shared")
            cdk.Tags.of(subnet).add("karpenter.sh/discovery", cluster_name)

        # --- VPC Endpoints (cost savings, required for private subnet ECR pulls) ---

        # S3 Gateway Endpoint (free)
        self.vpc.add_gateway_endpoint(
            "S3Endpoint",
            service=ec2.GatewayVpcEndpointAwsService.S3,
            subnets=[ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS)],
        )

        # ECR API Endpoint
        self.vpc.add_interface_endpoint(
            "EcrApiEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService.ECR,
            private_dns_enabled=True,
        )

        # ECR Docker Endpoint
        self.vpc.add_interface_endpoint(
            "EcrDockerEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
            private_dns_enabled=True,
        )

        # STS Endpoint (for IRSA / Pod Identity)
        self.vpc.add_interface_endpoint(
            "StsEndpoint",
            service=ec2.InterfaceVpcEndpointAwsService.STS,
            private_dns_enabled=True,
        )

        # --- Outputs ---
        cdk.CfnOutput(self, "VpcId", value=self.vpc.vpc_id, description="VPC ID")
        cdk.CfnOutput(self, "VpcCidr", value=self.vpc.vpc_cidr_block, description="VPC CIDR")

        cdk.CfnOutput(
            self,
            "PrivateSubnetIds",
            value=",".join([s.subnet_id for s in self.vpc.private_subnets]),
            description="Private subnet IDs (comma-separated)",
        )
        cdk.CfnOutput(
            self,
            "PublicSubnetIds",
            value=",".join([s.subnet_id for s in self.vpc.public_subnets]),
            description="Public subnet IDs (comma-separated)",
        )
