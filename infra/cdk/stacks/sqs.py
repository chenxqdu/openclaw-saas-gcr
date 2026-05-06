"""SQS stack for OpenClaw SaaS — mirrors CloudFormation template.

Includes: Usage Events Queue + DLQ, Karpenter Interruption Queue +
EventBridge rules for Spot/Health/Rebalance/StateChange.
"""
import aws_cdk as cdk
from aws_cdk import aws_sqs as sqs
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from constructs import Construct


class SqsStack(cdk.Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        config,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        cluster_name = config.cluster_name

        # =================================================================
        # Usage Events Queue + DLQ (for billing)
        # =================================================================
        self.usage_dlq = sqs.Queue(
            self,
            "UsageEventsDlq",
            queue_name=f"{cluster_name}-usage-events-dlq",
            retention_period=cdk.Duration.days(14),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
        )

        self.usage_queue = sqs.Queue(
            self,
            "UsageEventsQueue",
            queue_name=f"{cluster_name}-usage-events",
            retention_period=cdk.Duration.days(config.sqs_retention_period_days),
            visibility_timeout=cdk.Duration.seconds(config.sqs_visibility_timeout),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=config.sqs_max_receive_count,
                queue=self.usage_dlq,
            ),
        )

        # =================================================================
        # Karpenter Interruption Queue
        # =================================================================
        self.karpenter_queue = sqs.Queue(
            self,
            "KarpenterInterruptionQueue",
            queue_name=cluster_name,
            retention_period=cdk.Duration.seconds(300),
            encryption=sqs.QueueEncryption.SQS_MANAGED,
        )

        # Allow EventBridge to send messages
        self.karpenter_queue.add_to_resource_policy(
            iam.PolicyStatement(
                sid="AllowEventBridge",
                effect=iam.Effect.ALLOW,
                principals=[
                    iam.ServicePrincipal("events.amazonaws.com"),
                    iam.ServicePrincipal("sqs.amazonaws.com"),
                ],
                actions=["sqs:SendMessage"],
                resources=[self.karpenter_queue.queue_arn],
            )
        )

        # --- EventBridge Rules for Karpenter ---

        events.Rule(
            self,
            "ScheduledChangeRule",
            rule_name=f"{cluster_name}-ScheduledChange",
            event_pattern=events.EventPattern(
                source=["aws.health"],
                detail_type=["AWS Health Event"],
            ),
            targets=[targets.SqsQueue(self.karpenter_queue)],
        )

        events.Rule(
            self,
            "SpotInterruptionRule",
            rule_name=f"{cluster_name}-SpotInterruption",
            event_pattern=events.EventPattern(
                source=["aws.ec2"],
                detail_type=["EC2 Spot Instance Interruption Warning"],
            ),
            targets=[targets.SqsQueue(self.karpenter_queue)],
        )

        events.Rule(
            self,
            "RebalanceRule",
            rule_name=f"{cluster_name}-Rebalance",
            event_pattern=events.EventPattern(
                source=["aws.ec2"],
                detail_type=["EC2 Instance Rebalance Recommendation"],
            ),
            targets=[targets.SqsQueue(self.karpenter_queue)],
        )

        events.Rule(
            self,
            "InstanceStateChangeRule",
            rule_name=f"{cluster_name}-InstanceStateChange",
            event_pattern=events.EventPattern(
                source=["aws.ec2"],
                detail_type=["EC2 Instance State-change Notification"],
            ),
            targets=[targets.SqsQueue(self.karpenter_queue)],
        )

        # =================================================================
        # Outputs
        # =================================================================
        cdk.CfnOutput(self, "UsageQueueUrl", value=self.usage_queue.queue_url)
        cdk.CfnOutput(self, "UsageQueueArn", value=self.usage_queue.queue_arn)
        cdk.CfnOutput(self, "DlqUrl", value=self.usage_dlq.queue_url)
        cdk.CfnOutput(self, "DlqArn", value=self.usage_dlq.queue_arn)
        cdk.CfnOutput(
            self,
            "KarpenterInterruptionQueueName",
            value=self.karpenter_queue.queue_name,
        )
