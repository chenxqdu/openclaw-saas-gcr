"""Agent data models"""
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String

from api.database import Base
from api.config import settings


class AgentStatus(str, Enum):
    """Agent status types"""

    PENDING = "pending"
    RUNNING = "running"
    STOPPED = "stopped"
    ERROR = "error"


class Agent(Base):
    """Agent database model"""

    __tablename__ = "agents"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False)
    status = Column(String(50), default=AgentStatus.PENDING.value, nullable=False)
    channels = Column(JSON, default=list, nullable=False)
    llm_provider = Column(String(50), default="bedrock", nullable=False)
    llm_model = Column(String(255), nullable=True)
    custom_image = Column(String(512), nullable=True)
    custom_image_tag = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ─── LLM Provider definitions ───

LLM_PROVIDERS = {
    "bedrock": {
        "name": "AWS Bedrock",
        "env_keys": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_DEFAULT_REGION"],
        "optional_keys": [],
        "default_model": "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "models": [
            {"id": "global.anthropic.claude-opus-4-6-v1", "name": "Claude Opus 4.6"},
            {"id": "global.anthropic.claude-sonnet-4-6", "name": "Claude Sonnet 4.6"},
            {"id": "global.anthropic.claude-sonnet-4-5-20250929-v1:0", "name": "Claude Sonnet 4.5"},
            {"id": "global.anthropic.claude-sonnet-4-20250514-v1:0", "name": "Claude Sonnet 4"},
            {"id": "deepseek.v3.2", "name": "DeepSeek V3.2"},
            {"id": "minimax.minimax-m2.1", "name": "MiniMax M2.1"},
            {"id": "moonshotai.kimi-k2.5", "name": "Kimi K2.5"},
        ],
        "config_builder": lambda model: {
            "models": {
                "providers": {
                    "amazon-bedrock": {
                        "baseUrl": f"https://bedrock-runtime.{settings.AWS_REGION}.amazonaws.com",
                        "auth": "aws-sdk",
                        "api": "bedrock-converse-stream",
                        "models": [
                            {"id": model, "name": model, "input": ["text", "image"], "contextWindow": 200000, "maxTokens": 8192},
                        ],
                    }
                }
            }
        },
    },
    "openai": {
        "name": "OpenAI",
        "env_keys": ["OPENAI_API_KEY"],
        "optional_keys": [],
        "default_model": "gpt-4o",
        "models": [
            {"id": "gpt-4o", "name": "GPT-4o"},
            {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
            {"id": "o3-mini", "name": "o3-mini"},
        ],
        "config_builder": lambda model: {},  # OpenAI auto-configured via env var
    },
    "anthropic": {
        "name": "Anthropic",
        "env_keys": ["ANTHROPIC_API_KEY"],
        "optional_keys": [],
        "default_model": "claude-sonnet-4-5-20250929",
        "models": [
            {"id": "claude-opus-4-6", "name": "Claude Opus 4.6"},
            {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6"},
            {"id": "claude-sonnet-4-5-20250929", "name": "Claude Sonnet 4.5"},
            {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet 4"},
        ],
        "config_builder": lambda model: {},  # Anthropic auto-configured via env var
    },
    "openai-compatible": {
        "name": "OpenAI Compatible",
        "env_keys": ["CUSTOM_API_KEY"],
        "optional_keys": ["CUSTOM_BASE_URL", "CUSTOM_MODEL_ID"],
        "default_model": "custom-model",
        "models": [
            {"id": "custom-model", "name": "Custom Model (specify in API keys)"},
        ],
        "config_builder": lambda model: {},  # Built dynamically in k8s_client
    },
    "bedrock-irsa": {
        "name": "AWS Bedrock (Platform Managed)",
        "env_keys": [],
        "optional_keys": [],
        "default_model": "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "models": [
            {"id": "global.anthropic.claude-opus-4-6-v1", "name": "Claude Opus 4.6"},
            {"id": "global.anthropic.claude-sonnet-4-6", "name": "Claude Sonnet 4.6"},
            {"id": "global.anthropic.claude-sonnet-4-5-20250929-v1:0", "name": "Claude Sonnet 4.5"},
            {"id": "global.anthropic.claude-sonnet-4-20250514-v1:0", "name": "Claude Sonnet 4"},
            {"id": "deepseek.v3.2", "name": "DeepSeek V3.2"},
            {"id": "minimax.minimax-m2.1", "name": "MiniMax M2.1"},
            {"id": "moonshotai.kimi-k2.5", "name": "Kimi K2.5"},
        ],
        "config_builder": lambda model: {
            "models": {
                "providers": {
                    "amazon-bedrock": {
                        "baseUrl": f"https://bedrock-runtime.{settings.AWS_REGION}.amazonaws.com",
                        "auth": "aws-sdk",
                        "api": "bedrock-converse-stream",
                        "models": [
                            {"id": model, "name": model, "input": ["text", "image"], "contextWindow": 200000, "maxTokens": 8192},
                        ],
                    }
                }
            }
        },
    },
    "bedrock-apikey": {
        "name": "AWS Bedrock (API Key)",
        "env_keys": ["AWS_BEARER_TOKEN_BEDROCK"],
        "optional_keys": ["AWS_DEFAULT_REGION"],
        "default_model": "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "models": [
            {"id": "global.anthropic.claude-opus-4-6-v1", "name": "Claude Opus 4.6"},
            {"id": "global.anthropic.claude-sonnet-4-6", "name": "Claude Sonnet 4.6"},
            {"id": "global.anthropic.claude-sonnet-4-5-20250929-v1:0", "name": "Claude Sonnet 4.5"},
            {"id": "global.anthropic.claude-sonnet-4-20250514-v1:0", "name": "Claude Sonnet 4"},
            {"id": "deepseek.v3.2", "name": "DeepSeek V3.2"},
            {"id": "minimax.minimax-m2.1", "name": "MiniMax M2.1"},
            {"id": "moonshotai.kimi-k2.5", "name": "Kimi K2.5"},
        ],
        "config_builder": lambda model: {
            "models": {
                "providers": {
                    "amazon-bedrock": {
                        "baseUrl": f"https://bedrock-runtime.{settings.AWS_REGION}.amazonaws.com",
                        "auth": "aws-sdk",
                        "api": "bedrock-converse-stream",
                        "models": [
                            {"id": model, "name": model, "input": ["text", "image"], "contextWindow": 200000, "maxTokens": 8192},
                        ],
                    }
                }
            }
        },
    },
}


# Pydantic schemas
class AgentCreate(BaseModel):
    """Agent creation schema"""

    name: str = Field(..., min_length=3, max_length=63, pattern="^[a-z0-9-]+$")
    llm_provider: str = Field(default="bedrock-irsa", description="LLM provider: bedrock, openai, anthropic, bedrock-irsa, openai-compatible, bedrock-apikey")
    llm_model: Optional[str] = Field(default=None, description="Model ID (uses provider default if not specified)")
    llm_api_keys: Optional[Dict[str, str]] = Field(default=None, description="API keys for the LLM provider")
    enable_chromium: bool = Field(default=False, description="Enable Chromium browser sidecar for web automation")
    enable_gateway: bool = Field(default=False, description="Expose gateway to Internet via ALB ingress")
    custom_image: Optional[str] = Field(default=None, description="Custom container image repository (e.g. public.ecr.aws/xxx/openclaw-custom)")
    custom_image_tag: Optional[str] = Field(default=None, description="Custom container image tag (e.g. 2026.3.21). Defaults to 'latest' if custom_image is set")
    runtime_class_name: Optional[str] = Field(default=None, description="Kubernetes RuntimeClassName for the pod")
    node_selector: Optional[Dict[str, str]] = Field(default=None, description="Kubernetes nodeSelector key-value pairs")
    tolerations: Optional[List[Dict[str, str]]] = Field(default=None, description="Kubernetes tolerations list")
    config: Optional[Dict[str, Any]] = Field(default_factory=dict)


class AgentResponse(BaseModel):
    """Agent response schema"""

    id: int
    name: str
    tenant_id: int
    status: str
    channels: List[str]
    llm_provider: str
    llm_model: Optional[str]
    custom_image: Optional[str] = None
    custom_image_tag: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class AgentConfigUpdate(BaseModel):
    """Agent configuration update schema"""

    config: Dict[str, Any]


class LLMUpdateRequest(BaseModel):
    """Update LLM provider/model/keys for an agent"""

    llm_provider: Optional[str] = None
    llm_model: Optional[str] = None
    llm_api_keys: Optional[Dict[str, str]] = None


class ChannelBindRequest(BaseModel):
    """Channel binding request schema"""

    channel_type: str = Field(..., pattern="^(telegram|feishu|discord|whatsapp)$")
    credentials: Dict[str, str]
