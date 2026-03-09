"""Billing API - plan management"""
import sys
import os
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.tenant import Tenant, PlanType
from api.models.user import User
from api.services.auth_svc import get_current_user
from api.routers.tenants import get_user_tenant
from api.services.k8s_client import k8s_client

# Import billing quota module
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from billing.quota import check_quota, PLAN_LIMITS as TOKEN_LIMITS  # noqa: E402

router = APIRouter(tags=["billing"])

PLAN_LIMITS = {
    "free": {
        "max_agents": 1,
        "max_memory_per_agent": "4Gi",
        "max_cpu_per_agent": "2 vCPU",
        "max_tokens_per_month": TOKEN_LIMITS.get("free"),
        "token_enforcement": False,
        "price_monthly": 0,
    },
    "pro": {
        "max_agents": 10,
        "max_memory_per_agent": "4Gi",
        "max_cpu_per_agent": "2 vCPU",
        "max_tokens_per_month": TOKEN_LIMITS.get("pro"),
        "token_enforcement": False,
        "price_monthly": 99,
    },
    "enterprise": {
        "max_agents": None,  # Unlimited
        "max_memory_per_agent": "4Gi",
        "max_cpu_per_agent": "2 vCPU",
        "max_tokens_per_month": None,
        "token_enforcement": False,
        "price_monthly": None,  # Custom pricing
    },
}


@router.get("/api/v1/plans")
async def list_plans():
    """List available plans and their limits"""
    return {"plans": PLAN_LIMITS}


@router.get("/api/v1/tenants/{tenant_name}/billing")
async def get_billing(
    tenant_name: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get billing info for a tenant including usage and quota"""
    tenant, _role = await get_user_tenant(tenant_name, current_user, db, min_role="member")

    plan_info = PLAN_LIMITS.get(tenant.plan, PLAN_LIMITS["free"])

    # Check quota status
    quota_status = await check_quota(db, tenant_name, tenant.plan)

    # Calculate days remaining in current month
    now = datetime.utcnow()
    if now.month == 12:
        next_month = datetime(now.year + 1, 1, 1)
    else:
        next_month = datetime(now.year, now.month + 1, 1)
    days_until_reset = (next_month - now).days

    return {
        "tenant": tenant.name,
        "current_plan": tenant.plan,
        "limits": plan_info,
        "current_month_usage": {
            "tokens_used": quota_status.current_usage,
            "tokens_limit": quota_status.limit,
            "percentage_used": quota_status.percentage_used,
            "is_over_quota": quota_status.is_over_quota,
            "is_warning": quota_status.is_warning,
            "days_until_reset": days_until_reset,
        },
    }


@router.get("/api/v1/tenants/{tenant_name}/billing/quota")
async def get_quota_status(
    tenant_name: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get current quota status for a tenant"""
    tenant, _role = await get_user_tenant(tenant_name, current_user, db, min_role="member")

    quota_status = await check_quota(db, tenant_name, tenant.plan)

    return quota_status.to_dict()


@router.post("/api/v1/tenants/{tenant_name}/billing/upgrade")
async def upgrade_plan(
    tenant_name: str,
    plan: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upgrade tenant plan (MVP: direct upgrade without payment)"""
    if plan not in PLAN_LIMITS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid plan: {plan}")

    tenant, _role = await get_user_tenant(tenant_name, current_user, db, min_role="owner")

    old_plan = tenant.plan
    tenant.plan = plan
    await db.flush()

    # Update K8s ResourceQuota and LimitRange
    try:
        await k8s_client.update_resource_quota(tenant_name, plan)
        await k8s_client.update_limit_range(tenant_name, plan)
        await db.commit()
    except Exception as e:
        # Rollback DB change if K8s update fails
        tenant.plan = old_plan
        await db.commit()
        raise HTTPException(status_code=500, detail=f"Failed to update K8s resources: {str(e)}")

    return {
        "tenant": tenant.name,
        "old_plan": old_plan,
        "new_plan": plan,
        "limits": PLAN_LIMITS[plan],
        "message": f"Plan changed from {old_plan} to {plan}. K8s resources updated.",
    }
