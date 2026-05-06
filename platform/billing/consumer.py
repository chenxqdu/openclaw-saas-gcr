"""SQS consumer for usage events."""
import json
import os
import time
import asyncio
from typing import List, Dict
import boto3
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
try:
    from billing.models import UsageEvent
except ImportError:
    from models import UsageEvent, Base


class Config:
    """Configuration from environment variables."""

    SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL", "")
    AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", os.getenv("AWS_REGION", ""))
    DATABASE_URL = os.getenv("DATABASE_URL", "")
    POLL_WAIT_TIME = int(os.getenv("POLL_WAIT_TIME", "20"))  # Long polling
    MAX_MESSAGES = int(os.getenv("MAX_MESSAGES", "10"))
    BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10"))


class UsageConsumer:
    """Consume usage events from SQS and insert into database."""

    def __init__(self):
        self.sqs = boto3.client("sqs", region_name=Config.AWS_DEFAULT_REGION)
        self.queue_url = Config.SQS_QUEUE_URL

        # Create async database engine
        self.engine = create_async_engine(
            Config.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://"),
            echo=False,
        )
        self.async_session = sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def init_db(self):
        """Initialize database tables."""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def process_messages(self, messages: List[Dict]) -> List[str]:
        """Process a batch of SQS messages and return receipt handles for deletion."""
        events = []
        receipt_handles = []

        for message in messages:
            try:
                body = json.loads(message["Body"])

                # Create UsageEvent model
                event = UsageEvent(
                    tenant=body["tenant"],
                    agent=body["agent"],
                    model=body["model"],
                    provider=body["provider"],
                    input_tokens=body.get("input_tokens", 0),
                    output_tokens=body.get("output_tokens", 0),
                    cache_read=body.get("cache_read", 0),
                    cache_write=body.get("cache_write", 0),
                    total_tokens=body.get("total_tokens", 0),
                    timestamp=body["timestamp"],
                )
                events.append(event)
                receipt_handles.append(message["ReceiptHandle"])

            except (json.JSONDecodeError, KeyError) as e:
                print(f"Error parsing message: {e}")
                # Still add receipt handle to delete malformed messages
                receipt_handles.append(message["ReceiptHandle"])
                continue

        # Insert events into database
        if events:
            async with self.async_session() as session:
                try:
                    session.add_all(events)
                    await session.commit()
                    print(f"Inserted {len(events)} usage events")
                except Exception as e:
                    print(f"Error inserting events: {e}")
                    await session.rollback()
                    return []  # Don't delete messages if insert failed

        return receipt_handles

    def delete_messages(self, receipt_handles: List[str]):
        """Delete processed messages from SQS queue."""
        if not receipt_handles:
            return

        try:
            # Delete in batches of 10 (SQS limit)
            for i in range(0, len(receipt_handles), 10):
                batch = receipt_handles[i : i + 10]
                entries = [
                    {"Id": str(j), "ReceiptHandle": handle}
                    for j, handle in enumerate(batch)
                ]

                response = self.sqs.delete_message_batch(
                    QueueUrl=self.queue_url, Entries=entries
                )

                if "Failed" in response and response["Failed"]:
                    print(f"Failed to delete {len(response['Failed'])} messages")

        except Exception as e:
            print(f"Error deleting messages: {e}")

    async def run(self):
        """Main consumer loop."""
        print(f"Starting usage consumer")
        print(f"Queue URL: {Config.SQS_QUEUE_URL}")
        print(f"Database: {Config.DATABASE_URL}")

        # Initialize database
        await self.init_db()

        while True:
            try:
                # Long poll for messages
                response = self.sqs.receive_message(
                    QueueUrl=self.queue_url,
                    MaxNumberOfMessages=Config.MAX_MESSAGES,
                    WaitTimeSeconds=Config.POLL_WAIT_TIME,
                    AttributeNames=["All"],
                )

                messages = response.get("Messages", [])
                if not messages:
                    print("No messages received")
                    continue

                print(f"Received {len(messages)} messages")

                # Process messages
                receipt_handles = await self.process_messages(messages)

                # Delete processed messages
                self.delete_messages(receipt_handles)

            except Exception as e:
                print(f"Error in consumer loop: {e}")
                await asyncio.sleep(5)  # Wait before retrying


async def main():
    """Entry point."""
    if not Config.SQS_QUEUE_URL:
        print("Error: SQS_QUEUE_URL is required")
        return 1

    if not Config.DATABASE_URL:
        print("Error: DATABASE_URL is required")
        return 1

    consumer = UsageConsumer()
    await consumer.run()


if __name__ == "__main__":
    asyncio.run(main())
