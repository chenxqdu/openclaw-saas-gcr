"""Agent data models"""
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String

from api.database import Base


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
    llm_provider = Column(String(50), default="openai", nullable=False)
    llm_model = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ─── LLM Provider definitions ───
# NOTE: bedrock-irsa removed — IRSA is not available in AWS China regions.
# Bedrock (AKSK) kept — users can call Bedrock in global regions using their own keys.

LLM_PROVIDERS = {
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
        "config_builder": lambda model: {},
    },
    "anthropic": {
        "name": "Anthropic",
        "env_keys": ["ANTHROPIC_API_KEY"],
        "optional_keys": [],
        "default_model": "claude-sonnet-4-5-20250929",
        "models": [
            {"id": "claude-sonnet-4-5-20250929", "name": "Claude Sonnet 4.5"},
            {"id": "claude-sonnet-4-20250514", "name": "Claude Sonnet 4"},
            {"id": "claude-opus-4-6", "name": "Claude Opus 4"},
        ],
        "config_builder": lambda model: {},
    },
    "bedrock": {
        "name": "AWS Bedrock (AKSK)",
        "env_keys": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
        "optional_keys": ["AWS_DEFAULT_REGION"],
        "default_model": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "models": [
            {"id": "us.anthropic.claude-sonnet-4-5-20250929-v1:0", "name": "Claude Sonnet 4.5 (Bedrock)"},
            {"id": "us.anthropic.claude-sonnet-4-20250514-v1:0", "name": "Claude Sonnet 4 (Bedrock)"},
            {"id": "us.anthropic.claude-opus-4-6-v1:0", "name": "Claude Opus 4 (Bedrock)"},
            {"id": "us.meta.llama3-3-70b-instruct-v1:0", "name": "Llama 3.3 70B (Bedrock)"},
        ],
        "config_builder": lambda model: {
            "models": {
                "providers": {
                    "amazon-bedrock": {
                        "baseUrl": "https://bedrock-runtime.us-west-2.amazonaws.com",
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
    "openai-compatible": {
        "name": "OpenAI Compatible (Custom Endpoint)",
        "env_keys": ["OPENAI_API_KEY"],
        "optional_keys": ["OPENAI_BASE_URL"],
        "default_model": "gpt-4o",
        "models": [
            {"id": "gpt-4o", "name": "GPT-4o (or custom)"},
        ],
        "config_builder": lambda model: {},
    },
}


# Pydantic schemas
class AgentCreate(BaseModel):
    """Agent creation schema"""

    name: str = Field(..., min_length=3, max_length=63, pattern="^[a-z0-9-]+$")
    llm_provider: str = Field(default="openai", description="LLM provider: openai, anthropic, bedrock, openai-compatible")
    llm_model: Optional[str] = Field(default=None, description="Model ID (uses provider default if not specified)")
    llm_api_keys: Optional[Dict[str, str]] = Field(default=None, description="API keys for the LLM provider")
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
