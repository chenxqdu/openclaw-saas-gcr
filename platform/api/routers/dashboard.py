"""Dashboard aggregation endpoints — reduce multiple API round-trips to one."""
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.agent import Agent
from api.models.tenant import (
    Tenant, TenantResponse, TenantMember, TenantAllowedEmail,
    MemberResponse, AllowedEmailResponse,
)
from api.models.user import User
from api.services.auth_svc import get_current_user
from api.routers.tenants import get_user_tenant

router = APIRouter(prefix="/api/v1", tags=["dashboard"])


@router.get("/dashboard")
async def get_dashboard(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregated dashboard: tenant list + admin stats (if admin). Replaces 2 API calls."""
    from api.routers.tenants import list_tenants, platform_overview

    tenants = await list_tenants(current_user, db)

    admin_stats = None
    if getattr(current_user, 'is_platform_admin', False):
        try:
            admin_stats = await platform_overview(current_user, db)
        except Exception:
            pass

    return {
        "tenants": tenants,
        "admin_stats": admin_stats,
    }


@router.get("/tenants/{tenant_name}/dashboard")
async def get_tenant_dashboard(
    tenant_name: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregated tenant detail: info + agents + members + billing + allowed emails.
    Replaces 4-5 separate API calls with one."""
    tenant, role = await get_user_tenant(tenant_name, current_user, db)

    # --- Tenant info ---
    tenant_info = {
        "id": tenant.id,
        "name": tenant.name,
        "plan": tenant.plan,
        "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
    }

    # --- Agents with status ---
    from api.services.k8s_client import k8s_client
    agent_result = await db.execute(
        select(Agent).where(Agent.tenant_id == tenant.id)
    )
    agents_list = []
    for a in agent_result.scalars().all():
        gw = await k8s_client.get_agent_gateway_info(tenant_name, a.name)
        agents_list.append({
            "id": a.id,
            "name": a.name,
            "llm_provider": a.llm_provider,
            "llm_model": a.llm_model,
            "status": a.status,
            "channels": a.channels or [],
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "gateway_enabled": gw["gateway_enabled"],
            "gateway_url": gw["gateway_url"],
        })

    # --- Members ---
    owner_result = await db.execute(select(User).where(User.id == tenant.owner_id))
    owner = owner_result.scalar_one_or_none()

    members_list = []
    if owner:
        members_list.append({
            "user_id": owner.id,
            "email": owner.email,
            "display_name": owner.display_name,
            "role": "owner",
            "joined_at": tenant.created_at.isoformat() if tenant.created_at else None,
        })

    mem_result = await db.execute(
        select(TenantMember, User)
        .join(User, User.id == TenantMember.user_id)
        .where(TenantMember.tenant_id == tenant.id)
    )
    for membership, user in mem_result.all():
        members_list.append({
            "user_id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "role": membership.role,
            "joined_at": membership.created_at.isoformat() if membership.created_at else None,
        })

    # --- Billing ---
    from api.routers.billing import PLAN_LIMITS
    plan_limits = PLAN_LIMITS.get(tenant.plan, PLAN_LIMITS["free"])

    # Token usage this month
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    usage_result = await db.execute(text("""
        SELECT COALESCE(SUM(total_tokens), 0) as total_tokens,
               COALESCE(SUM(call_count), 0) as total_calls,
               COALESCE(SUM(estimated_cost), 0) as total_cost
        FROM daily_usage
        WHERE tenant = :tenant AND date >= :month_start
    """), {"tenant": tenant_name, "month_start": month_start})
    usage_row = usage_result.first()

    billing_info = {
        "plan": tenant.plan,
        "limits": plan_limits,
        "current_month": {
            "total_tokens": int(usage_row.total_tokens) if usage_row else 0,
            "total_calls": int(usage_row.total_calls) if usage_row else 0,
            "estimated_cost": round(float(usage_row.total_cost), 4) if usage_row else 0,
        },
        "agent_count": len(agents_list),
        "max_agents": plan_limits.get("max_agents"),
    }

    # --- Allowed emails (admin+ only) ---
    allowed_emails = []
    if role in ("owner", "admin") or getattr(current_user, 'is_platform_admin', False):
        ae_result = await db.execute(
            select(TenantAllowedEmail)
            .where(TenantAllowedEmail.tenant_id == tenant.id)
            .order_by(TenantAllowedEmail.created_at.desc())
        )
        for e in ae_result.scalars().all():
            allowed_emails.append({
                "id": e.id,
                "email": e.email,
                "role": e.role,
                "used": e.used,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            })

    return {
        "tenant": tenant_info,
        "role": role,
        "agents": agents_list,
        "members": members_list,
        "billing": billing_info,
        "allowed_emails": allowed_emails,
    }


@router.get("/tenants/{tenant_name}/billing/full")
async def get_billing_full(
    tenant_name: str,
    period_days: int = Query(default=30, ge=1, le=90),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Aggregated billing page: billing info + token usage breakdown in one call.
    Replaces 2 API calls (getBilling + getUsageTokens) with one."""
    import sys, os
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
    from billing.quota import check_quota
    from billing.models import DailyUsage
    from api.routers.billing import PLAN_LIMITS

    tenant, _role = await get_user_tenant(tenant_name, current_user, db, min_role="member")

    # --- Billing info (same as /billing endpoint) ---
    plan_info = PLAN_LIMITS.get(tenant.plan, PLAN_LIMITS["free"])
    quota_status = await check_quota(db, tenant_name, tenant.plan)

    now = datetime.utcnow()
    if now.month == 12:
        next_month = datetime(now.year + 1, 1, 1)
    else:
        next_month = datetime(now.year, now.month + 1, 1)
    days_until_reset = (next_month - now).days

    billing = {
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

    # --- Token usage (same as /usage/tokens endpoint) ---
    end_date = now
    start_date = (end_date - timedelta(days=period_days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    result = await db.execute(
        select(DailyUsage)
        .where(
            DailyUsage.tenant == tenant_name,
            DailyUsage.date >= start_date,
            DailyUsage.date <= end_date,
        )
        .order_by(DailyUsage.date.desc())
    )
    daily_records = result.scalars().all()

    total_tokens = sum(r.total_tokens for r in daily_records)
    total_input = sum(r.input_tokens for r in daily_records)
    total_output = sum(r.output_tokens for r in daily_records)
    total_cache_read = sum(r.cache_read for r in daily_records)
    total_cache_write = sum(r.cache_write for r in daily_records)
    total_calls = sum(r.call_count for r in daily_records)
    total_cost = sum(r.estimated_cost for r in daily_records)

    agent_breakdown = {}
    for record in daily_records:
        if record.agent not in agent_breakdown:
            agent_breakdown[record.agent] = {
                "agent_name": record.agent,
                "total_tokens": 0, "input_tokens": 0, "output_tokens": 0,
                "cache_read": 0, "cache_write": 0,
                "call_count": 0, "estimated_cost": 0.0,
            }
        ab = agent_breakdown[record.agent]
        ab["total_tokens"] += record.total_tokens
        ab["input_tokens"] += record.input_tokens
        ab["output_tokens"] += record.output_tokens
        ab["cache_read"] += record.cache_read
        ab["cache_write"] += record.cache_write
        ab["call_count"] += record.call_count
        ab["estimated_cost"] += record.estimated_cost

    model_breakdown = {}
    for record in daily_records:
        model_key = f"{record.provider}:{record.model}"
        if model_key not in model_breakdown:
            model_breakdown[model_key] = {
                "provider": record.provider, "model": record.model,
                "total_tokens": 0, "input_tokens": 0, "output_tokens": 0,
                "call_count": 0, "estimated_cost": 0.0,
            }
        mb = model_breakdown[model_key]
        mb["total_tokens"] += record.total_tokens
        mb["input_tokens"] += record.input_tokens
        mb["output_tokens"] += record.output_tokens
        mb["call_count"] += record.call_count
        mb["estimated_cost"] += record.estimated_cost

    daily_series = [
        {
            "date": r.date.isoformat(), "agent": r.agent,
            "model": r.model, "provider": r.provider,
            "total_tokens": r.total_tokens, "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens, "cache_read": r.cache_read,
            "cache_write": r.cache_write, "call_count": r.call_count,
            "estimated_cost": r.estimated_cost,
        }
        for r in daily_records
    ]

    usage = {
        "period_days": period_days,
        "summary": {
            "total_tokens": total_tokens, "input_tokens": total_input,
            "output_tokens": total_output,
            "cache_read_tokens": total_cache_read, "cache_write_tokens": total_cache_write,
            "total_calls": total_calls, "estimated_cost": round(total_cost, 2),
        },
        "by_agent": list(agent_breakdown.values()),
        "by_model": list(model_breakdown.values()),
        "daily": daily_series,
    }

    return {"billing": billing, "usage": usage}
