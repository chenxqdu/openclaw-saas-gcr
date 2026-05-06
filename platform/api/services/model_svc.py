"""
Model pool management for OpenClawInstance CRD.

Invariants
----------
1. CRD is source of truth. `agents.llm_model` in Postgres is a convenience
   mirror of the current default, updated after CRD patch succeeds.
   Do NOT add a reverse reconcile loop — CRD → DB is strictly one-way.

2. Pool lives in `agents.defaults.models` (map keyed by `<prefix>/<id>`).
   All 5 providers use this same map. Empty value `{}` means "in pool,
   no per-model overrides". The map is consumed by openclaw as the
   session/agent allowlist (see openclaw docs/concepts/models.md).

3. `models.providers.<key>` block is only written for providers that need
   runtime metadata (baseUrl/auth/apiKey/models[] with contextWindow):
     - bedrock, bedrock-apikey → `amazon-bedrock` block
     - openai-compatible       → `custom` block
     - openai, anthropic       → no block (built-in pi-ai catalog)

4. JSON merge-patch (RFC 7396): **maps merge**, **arrays replace**.
     - Removing a pool entry: set the map key to `null` in the patch.
     - Provider block's `models[]` array must be fully rewritten, with
       ALL sibling fields (baseUrl/auth/api/apiKey) copied verbatim from
       the current instance — partial blocks would clobber siblings.

5. Helpers are pure: take `instance` dict + provider + model_id, return
   patch body or view dict. Router layer handles I/O and DB commit.
"""

from typing import Any, Dict, List, Optional, Tuple

from api.models.agent import LLM_PROVIDERS

POOL_CAP = 3

# Prefix used in agents.defaults.model(s) keys.
_REF_PREFIX = {
    "bedrock":           "amazon-bedrock",
    "bedrock-apikey":    "amazon-bedrock",
    "openai-compatible": "custom",
    "openai":            "openai",
    "anthropic":         "anthropic",
}

# Key in models.providers.<key>. None → no block (built-in pi-ai catalog).
_PROVIDER_BLOCK_KEY = {
    "bedrock":           "amazon-bedrock",
    "bedrock-apikey":    "amazon-bedrock",
    "openai-compatible": "custom",
    "openai":            None,
    "anthropic":         None,
}


# ─── Small pure helpers ──────────────────────────────────────────────────

def ref_prefix(llm_provider: str) -> str:
    if llm_provider not in _REF_PREFIX:
        raise ValueError(f"Unknown provider: {llm_provider}")
    return _REF_PREFIX[llm_provider]


def make_ref(llm_provider: str, model_id: str) -> str:
    return f"{ref_prefix(llm_provider)}/{model_id}"


def parse_ref(ref: str) -> Tuple[str, str]:
    """Split 'prefix/bare_id' on the FIRST '/'."""
    if not ref or "/" not in ref:
        raise ValueError(f"Invalid model ref: {ref!r}")
    prefix, _, bare = ref.partition("/")
    if not prefix or not bare:
        raise ValueError(f"Invalid model ref: {ref!r}")
    return prefix, bare


def has_provider_block(llm_provider: str) -> bool:
    return _PROVIDER_BLOCK_KEY.get(llm_provider) is not None


def catalog_models(llm_provider: str) -> List[Dict[str, str]]:
    pdef = LLM_PROVIDERS.get(llm_provider)
    if not pdef:
        raise ValueError(f"Unknown provider: {llm_provider}")
    return list(pdef.get("models", []))


def validate_model_id(llm_provider: str, model_id: str) -> None:
    if not model_id or not model_id.strip():
        raise ValueError("Model id cannot be empty")
    if llm_provider == "openai-compatible":
        # User-driven catalog — any non-empty id is acceptable.
        return
    ids = {m["id"] for m in catalog_models(llm_provider)}
    if model_id not in ids:
        raise ValueError(
            f"Unknown model id '{model_id}' for provider {llm_provider}"
        )


# ─── Provider block entry builders ───────────────────────────────────────
# Shape mirrors what k8s_client.create_openclaw_instance writes at create time.

def _bedrock_entry(model_id: str) -> Dict[str, Any]:
    return {
        "id": model_id,
        "name": model_id,
        "input": ["text", "image"],
        "contextWindow": 200000,
        "maxTokens": 8192,
    }


def _custom_entry(model_id: str) -> Dict[str, Any]:
    return {
        "id": model_id,
        "name": model_id,
        "contextWindow": 200000,
        "maxTokens": 8192,
    }


def _entry_builder(llm_provider: str):
    if llm_provider in ("bedrock", "bedrock-apikey"):
        return _bedrock_entry
    if llm_provider == "openai-compatible":
        return _custom_entry
    raise ValueError(f"No provider block for {llm_provider}")


# ─── CRD dict accessors ──────────────────────────────────────────────────

def _raw(instance: Dict[str, Any]) -> Dict[str, Any]:
    return ((instance or {}).get("spec") or {}).get("config", {}).get("raw", {}) or {}


def _defaults(instance: Dict[str, Any]) -> Dict[str, Any]:
    return (_raw(instance).get("agents") or {}).get("defaults") or {}


def _current_primary_ref(instance: Dict[str, Any]) -> str:
    return ((_defaults(instance).get("model") or {}).get("primary") or "")


def _current_pool_map(instance: Dict[str, Any]) -> Dict[str, Any]:
    return _defaults(instance).get("models") or {}


def _current_provider_block(instance: Dict[str, Any], llm_provider: str) -> Dict[str, Any]:
    key = _PROVIDER_BLOCK_KEY.get(llm_provider)
    if key is None:
        return {}
    providers = (_raw(instance).get("models") or {}).get("providers") or {}
    return providers.get(key) or {}


# ─── Read view ───────────────────────────────────────────────────────────

def read_pool(instance: Dict[str, Any], llm_provider: str) -> Dict[str, Any]:
    """Parse the pool view from a CRD instance dict.

    Tolerates legacy agents that only set `agents.defaults.model.primary`
    with no `agents.defaults.models` map — the primary is surfaced as the
    sole pool member.
    """
    prefix = ref_prefix(llm_provider)
    primary_ref = _current_primary_ref(instance)
    primary_bare = ""
    if primary_ref:
        try:
            p, b = parse_ref(primary_ref)
            if p == prefix:
                primary_bare = b
        except ValueError:
            # Legacy bare-id primary (no prefix). Treat as matching this provider.
            if "/" not in primary_ref:
                primary_bare = primary_ref

    pool_map = _current_pool_map(instance)
    pool_ids: List[str] = []
    for k in pool_map.keys():
        try:
            p, b = parse_ref(k)
        except ValueError:
            continue
        if p == prefix:
            pool_ids.append(b)

    # Legacy / drift tolerance: ensure primary is in the pool view.
    if primary_bare and primary_bare not in pool_ids:
        pool_ids = [primary_bare, *pool_ids]

    # Dedup preserving order.
    seen = set()
    ordered: List[str] = []
    for mid in pool_ids:
        if mid in seen:
            continue
        seen.add(mid)
        ordered.append(mid)

    # Name lookup from catalog.
    catalog = {m["id"]: m.get("name", m["id"]) for m in catalog_models(llm_provider)}

    models = [
        {
            "id": mid,
            "name": catalog.get(mid, mid),
            "is_default": mid == primary_bare,
        }
        for mid in ordered
    ]

    available = [m for m in catalog_models(llm_provider) if m["id"] not in seen]

    return {
        "provider": llm_provider,
        "primary": primary_bare,
        "models": models,
        "available": available,
        "pool_cap": POOL_CAP,
    }


# ─── Patch builders ──────────────────────────────────────────────────────

def _wrap(raw_patch: Dict[str, Any]) -> Dict[str, Any]:
    return {"spec": {"config": {"mergeMode": "merge", "raw": raw_patch}}}


def _full_pool_map(pool_ids: List[str], llm_provider: str) -> Dict[str, Any]:
    """Build a complete `agents.defaults.models` map for a set of bare ids.

    Writing the full map on every mutation heals legacy agents whose primary
    was never persisted into the map at creation time.
    """
    return {make_ref(llm_provider, mid): {} for mid in pool_ids}


def build_set_default_patch(
    instance: Dict[str, Any], llm_provider: str, model_id: str
) -> Dict[str, Any]:
    """Patch body switching the default to `model_id`.

    Raises ValueError if model_id is not in the current pool.
    Also persists the full pool map so legacy agents (primary without map
    entry) are healed on first default switch.
    """
    validate_model_id(llm_provider, model_id)
    pool = read_pool(instance, llm_provider)
    pool_ids = [m["id"] for m in pool["models"]]
    if model_id not in pool_ids:
        raise ValueError(
            f"Model '{model_id}' is not in the pool — add it first"
        )
    return _wrap({
        "agents": {"defaults": {
            "model": {"primary": make_ref(llm_provider, model_id)},
            "models": _full_pool_map(pool_ids, llm_provider),
        }},
    })


def build_add_patch(
    instance: Dict[str, Any],
    llm_provider: str,
    model_id: str,
    set_default: bool,
) -> Dict[str, Any]:
    """Patch body adding `model_id` to the pool (optionally set as default).

    Validates: id acceptable for provider, not already in pool, pool < cap.
    """
    validate_model_id(llm_provider, model_id)
    pool = read_pool(instance, llm_provider)
    pool_ids = [m["id"] for m in pool["models"]]
    if model_id in set(pool_ids):
        raise ValueError(f"Model '{model_id}' is already in the pool")
    if len(pool_ids) >= POOL_CAP:
        raise ValueError(f"Pool is at capacity (max {POOL_CAP})")

    ref = make_ref(llm_provider, model_id)
    # Rewrite the full map (existing + new) so a legacy primary that's only
    # surfaced by read_pool's tolerance gets persisted into the CR.
    new_ids = [*pool_ids, model_id]
    raw_patch: Dict[str, Any] = {
        "agents": {"defaults": {"models": _full_pool_map(new_ids, llm_provider)}},
    }
    if set_default:
        raw_patch["agents"]["defaults"]["model"] = {"primary": ref}

    if has_provider_block(llm_provider):
        old_block = _current_provider_block(instance, llm_provider)
        existing_entries = list(old_block.get("models") or [])
        # Dedup by id — merge-patch replaces the array wholesale, so keep
        # existing entries intact and append the new one.
        if not any(e.get("id") == model_id for e in existing_entries):
            entry = _entry_builder(llm_provider)(model_id)
            existing_entries.append(entry)
        raw_patch["models"] = {"providers": {
            _PROVIDER_BLOCK_KEY[llm_provider]: {
                **old_block,
                "models": existing_entries,
            },
        }}

    return _wrap(raw_patch)


def build_remove_patch(
    instance: Dict[str, Any], llm_provider: str, model_id: str
) -> Dict[str, Any]:
    """Patch body removing `model_id` from the pool.

    Validates: in pool, not current default, pool size > 1.
    """
    validate_model_id(llm_provider, model_id)
    pool = read_pool(instance, llm_provider)
    pool_ids = [m["id"] for m in pool["models"]]
    if model_id not in set(pool_ids):
        raise ValueError(f"Model '{model_id}' is not in the pool")
    if model_id == pool["primary"]:
        raise ValueError(
            "Cannot remove the current default — switch default first"
        )
    if len(pool_ids) <= 1:
        raise ValueError("Cannot remove the last model in the pool")

    ref = make_ref(llm_provider, model_id)
    # Persist remaining entries (healing legacy) and explicitly null the
    # removed key so merge-patch drops it.
    remaining = [mid for mid in pool_ids if mid != model_id]
    models_patch = _full_pool_map(remaining, llm_provider)
    models_patch[ref] = None
    raw_patch: Dict[str, Any] = {
        "agents": {"defaults": {"models": models_patch}},
    }

    if has_provider_block(llm_provider):
        old_block = _current_provider_block(instance, llm_provider)
        existing_entries = list(old_block.get("models") or [])
        new_entries = [e for e in existing_entries if e.get("id") != model_id]
        raw_patch["models"] = {"providers": {
            _PROVIDER_BLOCK_KEY[llm_provider]: {
                **old_block,
                "models": new_entries,
            },
        }}

    return _wrap(raw_patch)
