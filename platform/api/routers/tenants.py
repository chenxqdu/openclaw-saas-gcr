"""Tenant management router"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.tenant import (
    Tenant, TenantCreate, TenantResponse, TenantMember, TenantRole,
    TenantAllowedEmail, AllowedEmailRequest, AllowedEmailResponse,
    InviteMemberRequest, MemberResponse, UpdateMemberRoleRequest,
)
from api.models.user import User
from api.services.auth_svc import get_current_user
from api.services.k8s_client import k8s_client

router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"])


async def get_user_tenant(
    tenant_name: str, user: User, db: AsyncSession, min_role: str = "viewer"
) -> tuple:
    """Get tenant if user has access. Returns (tenant, role).
    Platform admins have implicit owner access to all tenants."""
    ROLE_LEVEL = {"owner": 4, "admin": 3, "member": 2, "viewer": 1}

    result = await db.execute(
        select(Tenant).where(Tenant.name == tenant_name)
    )
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Platform admin has full access to everything
    if getattr(user, 'is_platform_admin', False):
        return tenant, "owner"

    # Tenant owner
    if tenant.owner_id == user.id:
        role = "owner"
    else:
        # Check membership
        mem_result = await db.execute(
            select(TenantMember).where(
                TenantMember.tenant_id == tenant.id,
                TenantMember.user_id == user.id,
            )
        )
        member = mem_result.scalar_one_or_none()
        if not member:
            raise HTTPException(status_code=404, detail="Tenant not found")
        role = member.role

    if ROLE_LEVEL.get(role, 0) < ROLE_LEVEL.get(min_role, 0):
        raise HTTPException(status_code=403, detail="Insufficient permissions")

    return tenant, role


@router.get("", response_model=List[TenantResponse])
async def list_tenants(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List tenants. Platform admin sees all; regular users see owned + member tenants."""

    # Platform admin sees everything
    if getattr(current_user, 'is_platform_admin', False):
        all_result = await db.execute(select(Tenant))
        all_tenants = all_result.scalars().all()
        result = []
        for t in all_tenants:
            resp = TenantResponse.model_validate(t)
            resp.role = "platform-admin"
            result.append(resp)
        return result

    # Regular user: owned + member tenants
    owned = await db.execute(
        select(Tenant).where(Tenant.owner_id == current_user.id)
    )
    owned_tenants = owned.scalars().all()

    # Get member tenants
    member_result = await db.execute(
        select(TenantMember).where(TenantMember.user_id == current_user.id)
    )
    memberships = {m.tenant_id: m.role for m in member_result.scalars().all()}

    if memberships:
        member_tenant_ids = [tid for tid in memberships if tid not in [t.id for t in owned_tenants]]
        if member_tenant_ids:
            mt = await db.execute(
                select(Tenant).where(Tenant.id.in_(member_tenant_ids))
            )
            member_tenants = mt.scalars().all()
        else:
            member_tenants = []
    else:
        member_tenants = []

    # Build response with roles
    result = []
    for t in owned_tenants:
        resp = TenantResponse.model_validate(t)
        resp.role = "owner"
        result.append(resp)
    for t in member_tenants:
        resp = TenantResponse.model_validate(t)
        resp.role = memberships.get(t.id, "member")
        result.append(resp)

    return result


@router.post("", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    tenant_data: TenantCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new tenant"""
    result = await db.execute(
        select(Tenant).where(Tenant.name == tenant_data.name)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Tenant name already exists")

    tenant = Tenant(name=tenant_data.name, owner_id=current_user.id)
    db.add(tenant)
    await db.flush()

    # Add allowed emails if provided
    if tenant_data.allowed_emails:
        for email in tenant_data.allowed_emails:
            entry = TenantAllowedEmail(
                tenant_id=tenant.id,
                email=email.lower().strip(),
                role=TenantRole.MEMBER.value,
                added_by=current_user.id,
            )
            db.add(entry)
        await db.flush()

    try:
        await k8s_client.create_namespace(tenant_data.name)
        await k8s_client.create_resource_quota(tenant_data.name, tenant.plan)
        await k8s_client.create_network_policy(tenant_data.name)
        await k8s_client.create_limit_range(tenant_data.name, tenant.plan)

        await db.commit()
        await db.refresh(tenant)

        resp = TenantResponse.model_validate(tenant)
        resp.role = "owner"
        return resp

    except Exception as e:
        await db.rollback()
        try:
            await k8s_client.delete_namespace(tenant_data.name)
        except:
            pass
        raise HTTPException(status_code=500, detail=f"Failed to create tenant resources: {str(e)}")


@router.delete("/{tenant_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant(
    tenant_name: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a tenant (owner only)"""
    tenant, role = await get_user_tenant(tenant_name, current_user, db, min_role="owner")

    try:
        # Delete all agents first (cascade)
        from api.models.agent import Agent
        agent_result = await db.execute(
            select(Agent).where(Agent.tenant_id == tenant.id)
        )
        for agent in agent_result.scalars().all():
            try:
                await k8s_client.delete_openclaw_instance(tenant_name, agent.name)
            except Exception:
                pass
            await db.delete(agent)

        # Delete all memberships
        mem_result = await db.execute(
            select(TenantMember).where(TenantMember.tenant_id == tenant.id)
        )
        for m in mem_result.scalars().all():
            await db.delete(m)

        # Delete K8s namespace
        await k8s_client.delete_namespace(tenant_name)

        # Delete tenant
        await db.delete(tenant)
        await db.commit()

    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to delete tenant: {str(e)}")


# ─── Tenant Members ───

@router.get("/{tenant_name}/members", response_model=list[MemberResponse])
async def list_members(
    tenant_name: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all members of a tenant (viewer+ can see)"""
    tenant, role = await get_user_tenant(tenant_name, current_user, db, min_role="viewer")

    # Get owner
    owner_result = await db.execute(select(User).where(User.id == tenant.owner_id))
    owner = owner_result.scalar_one_or_none()

    members = []
    if owner:
        members.append(MemberResponse(
            user_id=owner.id,
            email=owner.email,
            display_name=owner.display_name,
            role="owner",
            joined_at=tenant.created_at,
        ))

    # Get other members
    mem_result = await db.execute(
        select(TenantMember, User)
        .join(User, User.id == TenantMember.user_id)
        .where(TenantMember.tenant_id == tenant.id)
    )
    for membership, user in mem_result.all():
        members.append(MemberResponse(
            user_id=user.id,
            email=user.email,
            display_name=user.display_name,
            role=membership.role,
            joined_at=membership.created_at,
        ))

    return members


# ─── Platform Admin ───

@router.get("/admin/overview")
async def platform_overview(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Platform-wide overview (platform admin only)"""
    if not getattr(current_user, 'is_platform_admin', False):
        raise HTTPException(status_code=403, detail="Platform admin only")

    from api.models.agent import Agent
    from sqlalchemy import func, text

    # Counts
    tenant_count = (await db.execute(select(func.count(Tenant.id)))).scalar()
    agent_count = (await db.execute(select(func.count(Agent.id)))).scalar()
    user_count = (await db.execute(select(func.count(User.id)))).scalar()

    # Usage totals
    usage = await db.execute(text("""
        SELECT COALESCE(SUM(total_tokens), 0) as total_tokens,
               COALESCE(SUM(call_count), 0) as total_calls,
               COALESCE(SUM(estimated_cost), 0) as total_cost
        FROM daily_usage
        WHERE date >= date_trunc('month', CURRENT_DATE)
    """))
    row = usage.first()

    # Per-tenant breakdown
    per_tenant = await db.execute(text("""
        SELECT t.name, t.plan,
               COUNT(DISTINCT a.id) as agent_count,
               COALESCE(SUM(du.total_tokens), 0) as tokens_used,
               COALESCE(SUM(du.estimated_cost), 0) as cost
        FROM tenants t
        LEFT JOIN agents a ON a.tenant_id = t.id
        LEFT JOIN daily_usage du ON du.tenant = t.name
            AND du.date >= date_trunc('month', CURRENT_DATE)
        GROUP BY t.name, t.plan
        ORDER BY tokens_used DESC
    """))

    return {
        "total_tenants": tenant_count,
        "total_agents": agent_count,
        "total_users": user_count,
        "current_month": {
            "total_tokens": int(row.total_tokens) if row else 0,
            "total_calls": int(row.total_calls) if row else 0,
            "estimated_cost": round(float(row.total_cost), 4) if row else 0,
        },
        "tenants": [
            {
                "name": r.name,
                "plan": r.plan,
                "agent_count": r.agent_count,
                "tokens_used": int(r.tokens_used),
                "cost": round(float(r.cost), 4),
            }
            for r in per_tenant
        ],
    }


# ─── Allowed Emails (for signup control) ───

@router.post("/{tenant_name}/allowed-emails", status_code=status.HTTP_201_CREATED)
async def add_allowed_email(
    tenant_name: str,
    request: AllowedEmailRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add an email to the tenant's allowed signup list (admin+ only)"""
    tenant, role = await get_user_tenant(tenant_name, current_user, db, min_role="admin")

    # Check if already exists
    result = await db.execute(
        select(TenantAllowedEmail).where(
            TenantAllowedEmail.tenant_id == tenant.id,
            TenantAllowedEmail.email == request.email,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        if existing.used:
            raise HTTPException(status_code=400, detail="This email has already signed up")
        raise HTTPException(status_code=400, detail="Email already in allowed list")

    entry = TenantAllowedEmail(
        tenant_id=tenant.id,
        email=request.email,
        role=request.role,
        added_by=current_user.id,
    )
    db.add(entry)
    await db.flush()
    await db.refresh(entry)
    return AllowedEmailResponse.model_validate(entry)


@router.get("/{tenant_name}/allowed-emails", response_model=list[AllowedEmailResponse])
async def list_allowed_emails(
    tenant_name: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List allowed emails for a tenant (admin+ only)"""
    tenant, role = await get_user_tenant(tenant_name, current_user, db, min_role="admin")

    result = await db.execute(
        select(TenantAllowedEmail)
        .where(TenantAllowedEmail.tenant_id == tenant.id)
        .order_by(TenantAllowedEmail.created_at.desc())
    )
    return [AllowedEmailResponse.model_validate(e) for e in result.scalars().all()]


@router.delete("/{tenant_name}/allowed-emails/{email_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_allowed_email(
    tenant_name: str,
    email_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove an email from the allowed list (admin+ only). Cannot remove if already used."""
    tenant, role = await get_user_tenant(tenant_name, current_user, db, min_role="admin")

    result = await db.execute(
        select(TenantAllowedEmail).where(
            TenantAllowedEmail.id == email_id,
            TenantAllowedEmail.tenant_id == tenant.id,
        )
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Allowed email entry not found")
    if entry.used:
        raise HTTPException(status_code=400, detail="Cannot remove — email already used for signup")

    await db.delete(entry)
