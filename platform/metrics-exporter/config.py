"""Configuration for metrics exporter sidecar."""
import os


class Config:
    """Configuration loaded from environment variables."""

    # Tenant and agent identification
    TENANT_NAME = os.getenv("TENANT_NAME", "")
    AGENT_NAME = os.getenv("AGENT_NAME", "")

    # SQS configuration
    SQS_QUEUE_URL = os.getenv("SQS_QUEUE_URL", "")
    AWS_DEFAULT_REGION = os.getenv("AWS_DEFAULT_REGION", os.getenv("AWS_REGION", ""))

    # Scrape configuration
    SCAN_INTERVAL_SECONDS = int(os.getenv("SCAN_INTERVAL_SECONDS", "30"))
    OTEL_METRICS_PORT = int(os.getenv("OTEL_METRICS_PORT", "9090"))

    @classmethod
    def validate(cls):
        """Validate required configuration."""
        errors = []

        if not cls.TENANT_NAME:
            errors.append("TENANT_NAME is required")
        if not cls.AGENT_NAME:
            errors.append("AGENT_NAME is required")
        if not cls.SQS_QUEUE_URL:
            errors.append("SQS_QUEUE_URL is required")

        if errors:
            raise ValueError(f"Configuration errors: {', '.join(errors)}")

        return True
