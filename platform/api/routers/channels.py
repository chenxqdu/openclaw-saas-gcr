"""Channel management router - binds/unbinds channels by patching CRD"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.agent import Agent, ChannelBindRequest
from api.models.tenant import Tenant
from api.models.user import User
from api.services.auth_svc import get_current_user
from api.services.channel_svc import (
    build_crd_channel_patch,
    build_crd_channel_remove_patch,
    validate_channel_credentials,
)
from api.services.k8s_client import k8s_client

router = APIRouter(tags=["channels"])


async def get_agent_or_404(tenant_name: str, agent_id: int, current_user: User, db: AsyncSession):
    """Get tenant and agent or raise 404.
    Uses shared get_user_tenant for consistent RBAC (owner, member, platform-admin).
    """
    from api.routers.tenants import get_user_tenant
    tenant, _role = await get_user_tenant(tenant_name, current_user, db, min_role="member")

    result = await db.execute(
        select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant.id)
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    return tenant, agent


@router.post("/api/v1/tenants/{tenant_name}/agents/{agent_id}/channels")
async def bind_channel(
    tenant_name: str,
    agent_id: int,
    channel_request: ChannelBindRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Bind a channel to an agent by patching the OpenClawInstance CRD"""
    tenant, agent = await get_agent_or_404(tenant_name, agent_id, current_user, db)

    # Validate credentials
    try:
        validate_channel_credentials(channel_request.channel_type, channel_request.credentials)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    # Check if already bound — allow re-bind (update)
    is_update = channel_request.channel_type in agent.channels

    try:
        # Patch CRD to add channel config
        patch = build_crd_channel_patch(agent.name, channel_request.channel_type, channel_request.credentials)
        await k8s_client.patch_openclaw_instance(tenant_name, agent.name, patch)

        # Update DB (only add if not already there)
        if not is_update:
            agent.channels = agent.channels + [channel_request.channel_type]
        await db.commit()

        action = "updated" if is_update else "bound"
        return {
            "status": action,
            "agent_id": agent.id,
            "agent_name": agent.name,
            "channel_type": channel_request.channel_type,
            "message": f"Channel {channel_request.channel_type} bound. Pod will restart to pick up new config.",
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to bind channel: {str(e)}",
        )


@router.put("/api/v1/tenants/{tenant_name}/agents/{agent_id}/channels/{channel_type}")
async def update_channel(
    tenant_name: str,
    agent_id: int,
    channel_type: str,
    channel_request: ChannelBindRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing channel binding (re-patches CRD with new credentials)"""
    tenant, agent = await get_agent_or_404(tenant_name, agent_id, current_user, db)

    if channel_type not in agent.channels:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel {channel_type} not bound to agent",
        )

    # Validate new credentials
    try:
        validate_channel_credentials(channel_type, channel_request.credentials)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    try:
        # Patch CRD with updated channel config
        patch = build_crd_channel_patch(agent.name, channel_type, channel_request.credentials)
        await k8s_client.patch_openclaw_instance(tenant_name, agent.name, patch)

        return {
            "status": "updated",
            "agent_id": agent.id,
            "agent_name": agent.name,
            "channel_type": channel_type,
            "message": f"Channel {channel_type} updated. Pod will restart to pick up new config.",
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update channel: {str(e)}",
        )


@router.delete("/api/v1/tenants/{tenant_name}/agents/{agent_id}/channels/{channel_type}", status_code=status.HTTP_204_NO_CONTENT)
async def unbind_channel(
    tenant_name: str,
    agent_id: int,
    channel_type: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Unbind a channel from an agent"""
    tenant, agent = await get_agent_or_404(tenant_name, agent_id, current_user, db)

    if channel_type not in agent.channels:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel {channel_type} not bound to agent",
        )

    try:
        # Patch CRD to disable channel
        patch = build_crd_channel_remove_patch(channel_type)
        await k8s_client.patch_openclaw_instance(tenant_name, agent.name, patch)

        # Update DB
        agent.channels = [ch for ch in agent.channels if ch != channel_type]
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to unbind channel: {str(e)}",
        )
