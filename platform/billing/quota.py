"""Quota management for tenant usage limits."""
from datetime import datetime, timedelta
from typing import Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
try:
    from billing.models import DailyUsage
except ImportError:
    from models import DailyUsage


# Plan limits (tokens per month) — counting only, no enforcement
PLAN_LIMITS = {
    "free": 100_000,  # 100K tokens
    "pro": 10_000_000,  # 10M tokens
    "enterprise": None,  # Unlimited
}


class QuotaStatus:
    """Quota status for a tenant."""

    def __init__(
        self,
        tenant: str,
        plan: str,
        current_usage: int,
        limit: Optional[int],
        percentage_used: float,
    ):
        self.tenant = tenant
        self.plan = plan
        self.current_usage = current_usage
        self.limit = limit
        self.percentage_used = percentage_used
        self.is_over_quota = False  # Token counting only, no enforcement
        self.is_warning = limit is not None and percentage_used >= 80.0

    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            "tenant": self.tenant,
            "plan": self.plan,
            "current_usage": self.current_usage,
            "limit": self.limit,
            "percentage_used": round(self.percentage_used, 2),
            "is_over_quota": self.is_over_quota,
            "is_warning": self.is_warning,
        }


async def get_monthly_usage(session: AsyncSession, tenant: str) -> int:
    """Get total token usage for current month."""
    now = datetime.utcnow()
    month_start = datetime(now.year, now.month, 1)

    result = await session.execute(
        select(func.sum(DailyUsage.total_tokens)).where(
            DailyUsage.tenant == tenant, DailyUsage.date >= month_start
        )
    )

    total = result.scalar_one_or_none()
    return total or 0


async def check_quota(
    session: AsyncSession, tenant: str, plan: str = "free"
) -> QuotaStatus:
    """Check if tenant is within quota."""
    current_usage = await get_monthly_usage(session, tenant)
    limit = PLAN_LIMITS.get(plan)

    if limit is None:
        # Unlimited plan
        percentage_used = 0.0
    else:
        percentage_used = (current_usage / limit) * 100 if limit > 0 else 0.0

    return QuotaStatus(
        tenant=tenant,
        plan=plan,
        current_usage=current_usage,
        limit=limit,
        percentage_used=percentage_used,
    )


async def check_quota_before_usage(
    session: AsyncSession, tenant: str, plan: str, estimated_tokens: int
) -> bool:
    """Check if usage is allowed before making an API call.
    Currently always returns True — token counting only, no enforcement."""
    return True
