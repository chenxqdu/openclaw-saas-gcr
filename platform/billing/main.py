"""Entrypoint: runs both SQS consumer and usage aggregator concurrently."""
import asyncio
from consumer import UsageConsumer, Config as ConsumerConfig
from aggregator import UsageAggregator, Config as AggregatorConfig


async def main():
    if not ConsumerConfig.SQS_QUEUE_URL:
        print("Error: SQS_QUEUE_URL is required")
        return
    if not ConsumerConfig.DATABASE_URL:
        print("Error: DATABASE_URL is required")
        return

    AggregatorConfig.DATABASE_URL = ConsumerConfig.DATABASE_URL

    consumer = UsageConsumer()
    aggregator = UsageAggregator()

    print("Starting billing service (consumer + aggregator)")
    await asyncio.gather(
        consumer.run(),
        aggregator.run(),
    )


if __name__ == "__main__":
    asyncio.run(main())
