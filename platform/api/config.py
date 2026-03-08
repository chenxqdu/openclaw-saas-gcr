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

    # JWT Authentication
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24

    # Kubernetes
    K8S_IN_CLUSTER: bool = False

    # Admin seed (set via env or K8s secret)
    ADMIN_EMAIL: str = os.getenv("ADMIN_EMAIL", "")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "")

    # Logging
    LOG_LEVEL: str = "INFO"

    # AWS China Region settings
    AWS_REGION: str = os.getenv("AWS_REGION", "cn-northwest-1")
    AWS_PARTITION: str = os.getenv("AWS_PARTITION", "aws-cn")
    AWS_ACCOUNT_ID: str = os.getenv("AWS_ACCOUNT_ID", "735091234506")

    SQS_QUEUE_URL: str = os.getenv(
        "SQS_QUEUE_URL",
        "https://cn-northwest-1.queue.amazonaws.com.cn/735091234506/openclaw-saas-dev-usage-events"
    )

    ECR_REGISTRY: str = os.getenv(
        "ECR_REGISTRY",
        "735091234506.dkr.ecr.cn-northwest-1.amazonaws.com.cn"
    )

    METRICS_EXPORTER_TAG: str = os.getenv("METRICS_EXPORTER_TAG", "latest")

    @property
    def ecr_domain(self) -> str:
        return self.ECR_REGISTRY

    @property
    def sqs_url(self) -> str:
        return self.SQS_QUEUE_URL

    @property
    def metrics_exporter_image(self) -> str:
        return f"{self.ECR_REGISTRY}/openclaw-saas-dev-metrics-exporter:{self.METRICS_EXPORTER_TAG}"


settings = Settings()
