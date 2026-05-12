"""Agent management router"""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.agent import (
    Agent, AgentCreate, AgentConfigUpdate, AgentResponse, AgentStatus,
    LLMUpdateRequest, LLM_PROVIDERS,
)
from api.models.tenant import Tenant
from api.models.user import User
from api.services.auth_svc import get_current_user
from api.services.k8s_client import k8s_client
from api.routers.tenants import get_user_tenant

router = APIRouter(tags=["agents"])


async def get_tenant_or_404(tenant_name: str, current_user: User, db: AsyncSession, min_role: str = "member") -> Tenant:
    """Get tenant if user has access with required role"""
    tenant, role = await get_user_tenant(tenant_name, current_user, db, min_role=min_role)
    return tenant


@router.get("/api/v1/tenants/{tenant_name}/agents", response_model=List[AgentResponse])
async def list_agents(
    tenant_name: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all agents for a tenant"""
    tenant = await get_tenant_or_404(tenant_name, current_user, db)
    result = await db.execute(select(Agent).where(Agent.tenant_id == tenant.id))
    return result.scalars().all()


@router.post("/api/v1/tenants/{tenant_name}/agents", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def create_agent(
    tenant_name: str,
    agent_data: AgentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new agent with chosen LLM provider and model.

    LLM providers:

    - bedrock: Your own AWS Bedrock (provide AWS_ACCESS_KEY_ID + AWS_SECRET_ACCESS_KEY)
    - openai: OpenAI (provide OPENAI_API_KEY)
    - anthropic: Anthropic (provide ANTHROPIC_API_KEY)
    """
    tenant = await get_tenant_or_404(tenant_name, current_user, db, min_role="member")

    # Validate provider
    if agent_data.llm_provider not in LLM_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown LLM provider: {agent_data.llm_provider}. Supported: {', '.join(LLM_PROVIDERS.keys())}",
        )

    # Check uniqueness
    result = await db.execute(
        select(Agent).where(Agent.tenant_id == tenant.id, Agent.name == agent_data.name)
    )
    if result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Agent name already exists in this tenant")

    provider_def = LLM_PROVIDERS[agent_data.llm_provider]
    model = agent_data.llm_model or provider_def["default_model"]

    agent = Agent(
        name=agent_data.name,
        tenant_id=tenant.id,
        channels=[],
        llm_provider=agent_data.llm_provider,
        llm_model=model,
        custom_image=agent_data.custom_image,
        custom_image_tag=agent_data.custom_image_tag,
    )
    db.add(agent)
    await db.flush()

    try:
        await k8s_client.create_openclaw_instance(
            tenant_name=tenant_name,
            agent_name=agent_data.name,
            llm_provider=agent_data.llm_provider,
            llm_model=model,
            llm_api_keys=agent_data.llm_api_keys,
            enable_chromium=agent_data.enable_chromium,
            enable_gateway=agent_data.enable_gateway,
            custom_image=agent_data.custom_image,
            custom_image_tag=agent_data.custom_image_tag,
            runtime_class_name=agent_data.runtime_class_name,
            node_selector=agent_data.node_selector,
            tolerations=agent_data.tolerations,
        )
        agent.status = AgentStatus.RUNNING.value
        await db.commit()
        await db.refresh(agent)
        return agent

    except ValueError as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to create agent: {str(e)}")


@router.get("/api/v1/llm-providers")
async def list_llm_providers():
    """List available LLM providers and their models"""
    result = {}
    for key, defn in LLM_PROVIDERS.items():
        result[key] = {
            "name": defn["name"],
            "required_keys": defn["env_keys"],
            "optional_keys": defn.get("optional_keys", []),
            "default_model": defn["default_model"],
            "models": defn["models"],
        }
    return result


@router.get("/api/v1/tenants/{tenant_name}/agents/{agent_id}/status")
async def get_agent_status(
    tenant_name: str,
    agent_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get agent pod status from K8s"""
    tenant = await get_tenant_or_404(tenant_name, current_user, db)
    result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant.id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    try:
        pod_status = await k8s_client.get_pod_status(tenant_name, agent.name)
        # Also get CRD status
        instance = await k8s_client.get_openclaw_instance(tenant_name, agent.name)
        crd_phase = instance.get("status", {}).get("phase", "Unknown") if instance else "NotFound"
        crd_ready = instance.get("status", {}).get("ready", False) if instance else False

        gw = await k8s_client.get_agent_gateway_info(tenant_name, agent.name)

        return {
            "agent_id": agent.id,
            "agent_name": agent.name,
            "db_status": agent.status,
            "crd_phase": crd_phase,
            "crd_ready": crd_ready,
            "channels": agent.channels,
            "pod": pod_status,
            "gateway_enabled": gw["gateway_enabled"],
            "gateway_url": gw["gateway_url"],
        }
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get status: {str(e)}")


@router.put("/api/v1/tenants/{tenant_name}/agents/{agent_id}/config")
async def update_agent_config(
    tenant_name: str,
    agent_id: int,
    config_update: AgentConfigUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update agent configuration via CRD patch"""
    tenant = await get_tenant_or_404(tenant_name, current_user, db, min_role="member")
    result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant.id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    try:
        patch = {"spec": {"config": {"mergeMode": "merge", "raw": config_update.config}}}
        await k8s_client.patch_openclaw_instance(tenant_name, agent.name, patch)
        return {"status": "updated", "agent_id": agent.id}
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to update config: {str(e)}")


@router.delete("/api/v1/tenants/{tenant_name}/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    tenant_name: str,
    agent_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an agent (CRD + secrets + DB record)"""
    tenant = await get_tenant_or_404(tenant_name, current_user, db, min_role="member")
    result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant.id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    try:
        await k8s_client.delete_openclaw_instance(tenant_name, agent.name)
        await db.delete(agent)
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to delete agent: {str(e)}")


@router.get("/api/v1/tenants/{tenant_name}/agents/{agent_id}/logs")
async def get_agent_logs(
    tenant_name: str,
    agent_id: int,
    container: str = "openclaw",
    tail: int = 100,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get agent pod logs.

    Query params:
    - container: openclaw (default), metrics-exporter, gateway-proxy
    - tail: number of lines from end (default 100, max 1000)
    """
    tenant = await get_tenant_or_404(tenant_name, current_user, db)
    result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant.id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    tail = min(max(tail, 1), 1000)

    try:
        logs = await k8s_client.get_pod_logs(tenant_name, agent.name, container=container, tail_lines=tail)
        return {
            "agent_id": agent.id,
            "agent_name": agent.name,
            **logs,
        }
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get logs: {str(e)}")
