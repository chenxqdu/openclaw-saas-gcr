"""Tenant data models"""
from datetime import datetime
from enum import Enum
from typing import Optional, List

from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, UniqueConstraint

from api.database import Base


class PlanType(str, Enum):
    """Tenant plan types"""

    FREE = "free"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class TenantRole(str, Enum):
    """Roles within a tenant"""

    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class Tenant(Base):
    """Tenant database model"""

    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, index=True, nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    plan = Column(String(50), default=PlanType.FREE.value, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class TenantMember(Base):
    """Tenant membership — links users to tenants with roles"""

    __tablename__ = "tenant_members"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String(50), default=TenantRole.MEMBER.value, nullable=False)
    invited_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_tenant_member"),
    )


class TenantAllowedEmail(Base):
    """Allowed emails for tenant signup — only these emails can register"""

    __tablename__ = "tenant_allowed_emails"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    role = Column(String(50), default=TenantRole.MEMBER.value, nullable=False)
    added_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    used = Column(Boolean, default=False, nullable=False)  # True after user signs up
    used_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_tenant_allowed_email"),
    )


# Pydantic schemas
class TenantCreate(BaseModel):
    """Tenant creation schema"""

    name: str = Field(..., min_length=3, max_length=63, pattern="^[a-z0-9-]+$")
    allowed_emails: Optional[List[str]] = Field(default=None, description="Emails allowed to signup for this tenant")


class TenantResponse(BaseModel):
    """Tenant response schema"""

    id: int
    name: str
    owner_id: int
    plan: str
    created_at: datetime
    role: Optional[str] = None  # User's role in this tenant

    class Config:
        from_attributes = True


class InviteMemberRequest(BaseModel):
    """Invite a member to tenant"""

    email: EmailStr
    role: str = Field(default="member", pattern="^(admin|member)$")


class MemberResponse(BaseModel):
    """Member info"""

    user_id: int
    email: str
    display_name: Optional[str] = None
    role: str
    joined_at: datetime

    class Config:
        from_attributes = True


class UpdateMemberRoleRequest(BaseModel):
    """Update member role"""

    role: str = Field(..., pattern="^(admin|member)$")


class AllowedEmailRequest(BaseModel):
    """Add an allowed email to a tenant"""

    email: EmailStr
    role: str = Field(default="member", pattern="^(admin|member)$")


class AllowedEmailResponse(BaseModel):
    """Allowed email info"""

    id: int
    email: str
    role: str
    used: bool
    created_at: datetime
    used_at: Optional[datetime] = None

    class Config:
        from_attributes = True
