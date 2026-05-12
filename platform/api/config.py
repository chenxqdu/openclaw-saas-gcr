"""Application configuration"""
import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings"""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./dev.db"

    # JWT Authentication (REQUIRED — no insecure default)
    JWT_SECRET: str = os.getenv("JWT_SECRET", "")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24

    # Kubernetes
    K8S_IN_CLUSTER: bool = False

    # Admin seed (set via env or K8s secret)
    ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")

    # Logging
    LOG_LEVEL: str = "INFO"

    # AWS Region settings (REQUIRED — no hardcoded defaults)
    AWS_REGION: str = os.getenv("AWS_REGION", "")
    AWS_PARTITION: str = os.getenv("AWS_PARTITION", "aws")
    AWS_ACCOUNT_ID: str = os.getenv("AWS_ACCOUNT_ID", "")

    # SQS and ECR — must be set via env/K8s secret, no fallback
    SQS_QUEUE_URL: str = os.getenv("SQS_QUEUE_URL", "")
    ECR_REGISTRY: str = os.getenv("ECR_REGISTRY", "")

    # Bedrock IRSA role ARN — injected into tenant SA annotation for pod-level Bedrock access
    BEDROCK_ROLE_ARN: str = os.getenv("BEDROCK_ROLE_ARN", "")

    METRICS_EXPORTER_REPO: str = os.getenv("METRICS_EXPORTER_REPO", "openclaw-saas-metrics-exporter")
    METRICS_EXPORTER_TAG: str = os.getenv("METRICS_EXPORTER_TAG", "v0.3.2")

    # Available channels for this region (comma-separated, empty = all)
    AVAILABLE_CHANNELS: str = os.getenv("AVAILABLE_CHANNELS", "")

    # Custom agent image (overrides operator default ghcr.io/openclaw/openclaw)
    # Leave empty to use operator default. Set for custom images with pre-installed tools.
    DEFAULT_AGENT_IMAGE: str = os.getenv("DEFAULT_AGENT_IMAGE", "")
    DEFAULT_AGENT_IMAGE_TAG: str = os.getenv("DEFAULT_AGENT_IMAGE_TAG", "latest")

    @property
    def ecr_domain(self) -> str:
        return self.ECR_REGISTRY

    @property
    def sqs_url(self) -> str:
        return self.SQS_QUEUE_URL

    @property
    def metrics_exporter_image(self) -> str:
        return f"{self.ECR_REGISTRY}/{self.METRICS_EXPORTER_REPO}:{self.METRICS_EXPORTER_TAG}"


settings = Settings()
