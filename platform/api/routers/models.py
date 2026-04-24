"""Model pool management router — add/remove/set-default within a provider."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.database import get_db
from api.models.agent import (
    ModelPoolAddRequest,
    ModelPoolSetDefaultRequest,
)
from api.models.user import User
from api.routers.channels import get_agent_or_404
from api.services import model_svc
from api.services.auth_svc import get_current_user
from api.services.k8s_client import k8s_client

router = APIRouter(tags=["models"])


async def _require_instance(tenant_name: str, agent_name: str) -> dict:
    inst = await k8s_client.get_openclaw_instance(tenant_name, agent_name)
    if inst is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent instance not ready — try again after the pod starts",
        )
    return inst


@router.get("/api/v1/tenants/{tenant_name}/agents/{agent_id}/models")
async def get_agent_models(
    tenant_name: str,
    agent_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Read the current model pool for an agent."""
    _, agent = await get_agent_or_404(tenant_name, agent_id, current_user, db)
    inst = await _require_instance(tenant_name, agent.name)
    try:
        pool = model_svc.read_pool(inst, agent.llm_provider)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return pool


@router.post("/api/v1/tenants/{tenant_name}/agents/{agent_id}/models")
async def add_agent_model(
    tenant_name: str,
    agent_id: int,
    req: ModelPoolAddRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a model to the pool. Optionally set it as default."""
    _, agent = await get_agent_or_404(tenant_name, agent_id, current_user, db)
    inst = await _require_instance(tenant_name, agent.name)

    try:
        patch = model_svc.build_add_patch(
            inst, agent.llm_provider, req.model_id, req.set_default
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    try:
        await k8s_client.patch_openclaw_instance(tenant_name, agent.name, patch)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to patch CRD: {e}",
        )

    if req.set_default:
        agent.llm_model = req.model_id
        try:
            await db.commit()
        except Exception:
            await db.rollback()
            # CRD is source of truth; DB mirror will re-sync on next read.

    return {
        "status": "added",
        "agent_id": agent.id,
        "agent_name": agent.name,
        "model_id": req.model_id,
        "set_default": req.set_default,
        "message": "Model added to pool. Pod will restart to pick up new config.",
    }


@router.put("/api/v1/tenants/{tenant_name}/agents/{agent_id}/models/default")
async def set_agent_model_default(
    tenant_name: str,
    agent_id: int,
    req: ModelPoolSetDefaultRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Switch the default model. Model must already be in the pool."""
    _, agent = await get_agent_or_404(tenant_name, agent_id, current_user, db)
    inst = await _require_instance(tenant_name, agent.name)

    try:
        pool = model_svc.read_pool(inst, agent.llm_provider)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    if req.model_id == pool["primary"]:
        return {
            "status": "noop",
            "agent_id": agent.id,
            "agent_name": agent.name,
            "model_id": req.model_id,
            "message": "Already the default model.",
        }

    try:
        patch = model_svc.build_set_default_patch(
            inst, agent.llm_provider, req.model_id
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    try:
        await k8s_client.patch_openclaw_instance(tenant_name, agent.name, patch)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to patch CRD: {e}",
        )

    agent.llm_model = req.model_id
    try:
        await db.commit()
    except Exception:
        await db.rollback()

    return {
        "status": "updated",
        "agent_id": agent.id,
        "agent_name": agent.name,
        "model_id": req.model_id,
        "message": "Default model switched. Pod will restart to pick up new config.",
    }


@router.delete("/api/v1/tenants/{tenant_name}/agents/{agent_id}/models/{model_id:path}")
async def remove_agent_model(
    tenant_name: str,
    agent_id: int,
    model_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a model from the pool. Must not be the current default."""
    _, agent = await get_agent_or_404(tenant_name, agent_id, current_user, db)
    inst = await _require_instance(tenant_name, agent.name)

    try:
        patch = model_svc.build_remove_patch(inst, agent.llm_provider, model_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))

    try:
        await k8s_client.patch_openclaw_instance(tenant_name, agent.name, patch)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to patch CRD: {e}",
        )

    return {
        "status": "removed",
        "agent_id": agent.id,
        "agent_name": agent.name,
        "model_id": model_id,
        "message": "Model removed from pool. Pod will restart to pick up new config.",
    }
