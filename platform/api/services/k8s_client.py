"""Kubernetes client for managing resources"""
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from jinja2 import Environment, FileSystemLoader
from kubernetes_asyncio import client, config
from kubernetes_asyncio.client import ApiClient

from api.config import settings

# Template directory
TEMPLATE_DIR = Path(__file__).parent.parent / "templates"


class K8sClient:
    """Kubernetes async client wrapper"""

    def __init__(self):
        self._initialized = False
        self._api_client: Optional[ApiClient] = None
        self._core_v1: Optional[client.CoreV1Api] = None
        self._custom_objects: Optional[client.CustomObjectsApi] = None
        self._networking_v1: Optional[client.NetworkingV1Api] = None
        self.jinja_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)))

    async def initialize(self):
        """Initialize Kubernetes client"""
        if self._initialized:
            return

        if settings.K8S_IN_CLUSTER:
            config.load_incluster_config()
        else:
            await config.load_kube_config()

        self._api_client = ApiClient()
        self._core_v1 = client.CoreV1Api(self._api_client)
        self._custom_objects = client.CustomObjectsApi(self._api_client)
        self._networking_v1 = client.NetworkingV1Api(self._api_client)
        self._initialized = True

    async def close(self):
        """Close Kubernetes client"""
        if self._api_client:
            await self._api_client.close()
            self._initialized = False

    def render_template(self, template_name: str, **kwargs) -> str:
        """Render Jinja2 template"""
        template = self.jinja_env.get_template(template_name)
        return template.render(**kwargs)

    # ─── Namespace ───

    async def create_namespace(self, tenant_name: str) -> dict:
        """Create namespace for tenant"""
        await self.initialize()
        namespace_yaml = self.render_template("namespace.yaml", tenant_name=tenant_name)
        namespace_obj = yaml.safe_load(namespace_yaml)
        try:
            result = await self._core_v1.create_namespace(body=namespace_obj)
            return {"status": "created", "name": result.metadata.name}
        except client.exceptions.ApiException as e:
            if e.status == 409:
                raise ValueError(f"Namespace tenant-{tenant_name} already exists")
            raise

    async def delete_namespace(self, tenant_name: str) -> dict:
        """Delete namespace for tenant"""
        await self.initialize()
        namespace_name = f"tenant-{tenant_name}"
        try:
            await self._core_v1.delete_namespace(name=namespace_name)
            return {"status": "deleted", "name": namespace_name}
        except client.exceptions.ApiException as e:
            if e.status == 404:
                raise ValueError(f"Namespace {namespace_name} not found")
            raise

    # ─── ResourceQuota / LimitRange / NetworkPolicy ───

    async def create_resource_quota(self, tenant_name: str, plan: str) -> dict:
        await self.initialize()
        quota_yaml = self.render_template("resource_quota.yaml", tenant_name=tenant_name, plan=plan)
        quota_obj = yaml.safe_load(quota_yaml)
        try:
            result = await self._core_v1.create_namespaced_resource_quota(
                namespace=f"tenant-{tenant_name}", body=quota_obj
            )
            return {"status": "created", "name": result.metadata.name}
        except client.exceptions.ApiException as e:
            if e.status == 409:
                return {"status": "exists", "name": quota_obj["metadata"]["name"]}
            raise

    async def create_network_policy(self, tenant_name: str) -> dict:
        await self.initialize()
        policy_yaml = self.render_template("network_policy.yaml", tenant_name=tenant_name)
        policy_obj = yaml.safe_load(policy_yaml)
        try:
            result = await self._networking_v1.create_namespaced_network_policy(
                namespace=f"tenant-{tenant_name}", body=policy_obj
            )
            return {"status": "created", "name": result.metadata.name}
        except client.exceptions.ApiException as e:
            if e.status == 409:
                return {"status": "exists", "name": policy_obj["metadata"]["name"]}
            raise

    async def create_limit_range(self, tenant_name: str, plan: str) -> dict:
        await self.initialize()
        limit_yaml = self.render_template("limit_range.yaml", tenant_name=tenant_name, plan=plan)
        limit_obj = yaml.safe_load(limit_yaml)
        try:
            result = await self._core_v1.create_namespaced_limit_range(
                namespace=f"tenant-{tenant_name}", body=limit_obj
            )
            return {"status": "created", "name": result.metadata.name}
        except client.exceptions.ApiException as e:
            if e.status == 409:
                return {"status": "exists", "name": limit_obj["metadata"]["name"]}
            raise

    async def update_resource_quota(self, tenant_name: str, plan: str) -> dict:
        """Update ResourceQuota for a tenant namespace when plan changes"""
        await self.initialize()
        quota_yaml = self.render_template("resource_quota.yaml", tenant_name=tenant_name, plan=plan)
        quota_obj = yaml.safe_load(quota_yaml)
        ns = f"tenant-{tenant_name}"
        try:
            result = await self._core_v1.replace_namespaced_resource_quota(
                name="tenant-quota", namespace=ns, body=quota_obj
            )
            return {"status": "updated", "name": result.metadata.name}
        except client.exceptions.ApiException as e:
            if e.status == 404:
                # Doesn't exist yet, create it
                return await self.create_resource_quota(tenant_name, plan)
            raise

    async def update_limit_range(self, tenant_name: str, plan: str) -> dict:
        """Update LimitRange for a tenant namespace when plan changes"""
        await self.initialize()
        limit_yaml = self.render_template("limit_range.yaml", tenant_name=tenant_name, plan=plan)
        limit_obj = yaml.safe_load(limit_yaml)
        ns = f"tenant-{tenant_name}"
        try:
            result = await self._core_v1.replace_namespaced_limit_range(
                name="tenant-limits", namespace=ns, body=limit_obj
            )
            return {"status": "updated", "name": result.metadata.name}
        except client.exceptions.ApiException as e:
            if e.status == 404:
                return await self.create_limit_range(tenant_name, plan)
            raise

    # ─── Secrets ───

    async def create_secret(self, tenant_name: str, secret_name: str, data: Dict[str, str]) -> dict:
        await self.initialize()
        secret = client.V1Secret(
            metadata=client.V1ObjectMeta(name=secret_name),
            string_data=data,
            type="Opaque",
        )
        try:
            result = await self._core_v1.create_namespaced_secret(
                namespace=f"tenant-{tenant_name}", body=secret
            )
            return {"status": "created", "name": result.metadata.name}
        except client.exceptions.ApiException as e:
            if e.status == 409:
                result = await self._core_v1.patch_namespaced_secret(
                    name=secret_name, namespace=f"tenant-{tenant_name}", body=secret
                )
                return {"status": "updated", "name": result.metadata.name}
            raise

    async def delete_secret(self, tenant_name: str, secret_name: str) -> dict:
        await self.initialize()
        try:
            await self._core_v1.delete_namespaced_secret(
                name=secret_name, namespace=f"tenant-{tenant_name}"
            )
            return {"status": "deleted", "name": secret_name}
        except client.exceptions.ApiException as e:
            if e.status == 404:
                return {"status": "not_found", "name": secret_name}
            raise

    # ─── OpenClawInstance CRD ───

    CRD_GROUP = "openclaw.rocks"
    CRD_VERSION = "v1alpha1"
    CRD_PLURAL = "openclawinstances"

    async def create_openclaw_instance(
        self,
        tenant_name: str,
        agent_name: str,
        llm_provider: str = "openai-compatible",
        llm_model: Optional[str] = None,
        llm_api_keys: Optional[Dict[str, str]] = None,
        channel_config: Optional[Dict] = None,
        enable_chromium: bool = False,
        custom_image: Optional[str] = None,
        custom_image_tag: Optional[str] = None,
    ) -> dict:
        """Create OpenClawInstance CRD + agent-keys secret."""
        from api.models.agent import LLM_PROVIDERS

        await self.initialize()
        namespace = f"tenant-{tenant_name}"

        # Resolve image: explicit param > platform default > operator default (ghcr.io/openclaw/openclaw)
        effective_image = custom_image or settings.DEFAULT_AGENT_IMAGE or None
        effective_image_tag = custom_image_tag or (settings.DEFAULT_AGENT_IMAGE_TAG if effective_image else None)

        provider_def = LLM_PROVIDERS.get(llm_provider)
        if not provider_def:
            raise ValueError(f"Unknown LLM provider: {llm_provider}. Supported: {', '.join(LLM_PROVIDERS.keys())}")

        model = llm_model or provider_def["default_model"]

        # 1) Create keys secret with LLM API keys (if provided)
        secret_data = {}
        if llm_api_keys:
            # Validate required keys
            required = set(provider_def["env_keys"])
            provided = set(llm_api_keys.keys())
            missing = required - provided
            if missing:
                raise ValueError(f"Missing required API keys for {llm_provider}: {', '.join(missing)}")
            # Only store actual secrets in K8s Secret (not config values like URLs)
            non_secret_keys = {"CUSTOM_BASE_URL", "CUSTOM_MODEL_ID", "AWS_DEFAULT_REGION"}
            secret_data = {k: v for k, v in llm_api_keys.items() if k not in non_secret_keys}

        await self.create_secret(tenant_name, f"{agent_name}-keys", secret_data)

        # 2) Build openclaw.json config
        #    - For providers with env var auth (openai, anthropic): just set model name
        #    - For bedrock: need explicit provider config
        model_prefix = {
            "bedrock": f"amazon-bedrock/{model}",
            "bedrock-apikey": f"amazon-bedrock/{model}",
            "openai": model,
            "anthropic": model,
            "openai-compatible": f"custom/{model}",
        }.get(llm_provider, model)

        raw_config = {
            "agents": {
                "defaults": {
                    "model": {
                        "primary": model_prefix,
                    },
                },
            },
        }

        # Add provider-specific config (e.g., Bedrock needs explicit provider block)
        provider_config = provider_def["config_builder"](model)
        raw_config.update(provider_config)

        # OpenAI-compatible: build custom provider config from user-supplied keys
        if llm_provider == "openai-compatible" and llm_api_keys:
            base_url = llm_api_keys.get("CUSTOM_BASE_URL", "")
            model_id = llm_api_keys.get("CUSTOM_MODEL_ID", model)
            raw_config["models"] = {
                "providers": {
                    "custom": {
                        "baseUrl": base_url,
                        "apiKey": "${CUSTOM_API_KEY}",
                        "api": "openai-completions",
                        "models": [
                            {"id": model_id, "name": model_id, "contextWindow": 200000, "maxTokens": 8192},
                        ],
                    }
                }
            }
            raw_config["agents"]["defaults"]["model"]["primary"] = f"custom/{model_id}"

        # Bedrock API Key: override region in baseUrl from user-supplied AWS_DEFAULT_REGION
        if llm_provider == "bedrock-apikey" and llm_api_keys:
            region = llm_api_keys.get("AWS_DEFAULT_REGION", "us-west-2")
            raw_config["models"]["providers"]["amazon-bedrock"]["baseUrl"] = (
                f"https://bedrock-runtime.{region}.amazonaws.com"
            )

        # Browser config: when Chromium sidecar is enabled, configure CDP connection
        # Use a custom profile name (not "openclaw" which is auto-managed and tries to launch)
        # Use pod hostname instead of 127.0.0.1 — OpenClaw treats loopback as local and
        # rejects it when port is occupied by the sidecar ("not by openclaw").
        # Pod hostname (e.g. agent-0308-0) is non-loopback → treated as remote CDP.
        if enable_chromium:
            raw_config["browser"] = {
                "defaultProfile": "browserless",
                "remoteCdpTimeoutMs": 3000,
                "remoteCdpHandshakeTimeoutMs": 5000,
                "profiles": {
                    "browserless": {
                        "cdpUrl": f"http://{agent_name}-0:9222",
                        "color": "#4285F4",
                    },
                },
            }

        if channel_config:
            raw_config["channels"] = channel_config

        # Enable wecom plugin (official WeCom channel by Tencent)
        raw_config.setdefault("plugins", {"allow": [], "entries": {}})
        raw_config["plugins"].setdefault("allow", [])
        raw_config["plugins"].setdefault("entries", {})
        raw_config["plugins"].setdefault("load", {}).setdefault("paths", [])
        
        if "wecom-openclaw-plugin" not in raw_config["plugins"]["allow"]:
            raw_config["plugins"]["allow"].append("wecom-openclaw-plugin")
        raw_config["plugins"]["entries"]["wecom-openclaw-plugin"] = {
            "enabled": True,
        }
    
        wecom_plugin_path = "/home/openclaw/.openclaw/node_modules/@wecom/wecom-openclaw-plugin"
        raw_config["plugins"]["load"]["paths"].append(wecom_plugin_path)

        # Enable diagnostics-otel plugin so OpenClaw exports metrics/traces
        # to the otel-collector sidecar (managed by operator)
    
        if "diagnostics-otel" not in raw_config["plugins"]["allow"]:
            raw_config["plugins"]["allow"].append("diagnostics-otel")
        raw_config["plugins"]["entries"]["diagnostics-otel"] = {
            "enabled": True,
        }
        raw_config["diagnostics"] = {
            "enabled": True,
            "otel": {
                "enabled": True,
                "endpoint": "http://localhost:4318",
                "protocol": "http/protobuf",
                "serviceName": f"{tenant_name}-{agent_name}",
                "traces": False,
                "metrics": True,
                "logs": False,
                "flushIntervalMs": 30000,
            },
        }
    

        raw_config["tools"] = {
            "exec": {"security": "full", "ask": "off"},
        }

        # Gateway: local mode required so sessions_spawn (acpx) can connect
        # without triggering "pairing required" (1008) rejection.
        raw_config["gateway"] = {
            "mode": "local",
            "auth": {
                "mode": "none",
            },
        }

        # 3) Build CRD body
        sqs_queue_url = settings.sqs_url
        body = {
            "apiVersion": f"{self.CRD_GROUP}/{self.CRD_VERSION}",
            "kind": "OpenClawInstance",
            "metadata": {
                "name": agent_name,
                "namespace": namespace,
                "labels": {
                    "app.kubernetes.io/managed-by": "openclaw-saas",
                    "openclaw-saas/tenant": tenant_name,
                    "openclaw-saas/agent": agent_name,
                },
            },
            "spec": {
                **({"image": {
                    "repository": effective_image,
                    "tag": effective_image_tag or "latest",
                    "pullPolicy": "Always",
                }} if effective_image else {}),
                "plugins": ["@wecom/wecom-openclaw-plugin"],
                "envFrom": [{"secretRef": {"name": f"{agent_name}-keys"}}],
                "env": [{"name": "NODE_OPTIONS", "value": "--max-old-space-size=3072"}],
                "config": {
                    "mergeMode": "merge",
                    "raw": raw_config,
                },
                "storage": {"persistence": {"enabled": True, "size": "50Gi"}},
                "chromium": {
                    "enabled": enable_chromium,
                    **({"extraEnv": [
                        {"name": "TIMEOUT", "value": "300000"},
                        {"name": "CONNECTION_TIMEOUT", "value": "120000"},
                    ]} if enable_chromium else {}),
                },
                "resources": {
                    "requests": {"cpu": "500m", "memory": "2Gi"},
                    "limits": {"cpu": "2", "memory": "4Gi"},
                },
            },
        }

        try:
            result = await self._custom_objects.create_namespaced_custom_object(
                group=self.CRD_GROUP,
                version=self.CRD_VERSION,
                namespace=namespace,
                plural=self.CRD_PLURAL,
                body=body,
            )
            return {"status": "created", "name": agent_name}
        except client.exceptions.ApiException as e:
            if e.status == 409:
                raise ValueError(f"Agent {agent_name} already exists")
            raise

    async def get_openclaw_instance(self, tenant_name: str, agent_name: str) -> Optional[dict]:
        """Get OpenClawInstance CRD"""
        await self.initialize()
        try:
            return await self._custom_objects.get_namespaced_custom_object(
                group=self.CRD_GROUP,
                version=self.CRD_VERSION,
                namespace=f"tenant-{tenant_name}",
                plural=self.CRD_PLURAL,
                name=agent_name,
            )
        except client.exceptions.ApiException as e:
            if e.status == 404:
                return None
            raise

    async def patch_openclaw_instance(self, tenant_name: str, agent_name: str, patch: dict) -> dict:
        """Patch OpenClawInstance CRD (merge patch)"""
        await self.initialize()
        try:
            result = await self._custom_objects.patch_namespaced_custom_object(
                group=self.CRD_GROUP,
                version=self.CRD_VERSION,
                namespace=f"tenant-{tenant_name}",
                plural=self.CRD_PLURAL,
                name=agent_name,
                body=patch,
                _content_type="application/merge-patch+json",
            )
            return {"status": "patched", "name": agent_name}
        except client.exceptions.ApiException as e:
            if e.status == 404:
                raise ValueError(f"Agent {agent_name} not found")
            raise

    async def delete_openclaw_instance(self, tenant_name: str, agent_name: str) -> dict:
        """Delete OpenClawInstance CRD + associated secrets"""
        await self.initialize()
        namespace = f"tenant-{tenant_name}"
        try:
            await self._custom_objects.delete_namespaced_custom_object(
                group=self.CRD_GROUP,
                version=self.CRD_VERSION,
                namespace=namespace,
                plural=self.CRD_PLURAL,
                name=agent_name,
            )
        except client.exceptions.ApiException as e:
            if e.status != 404:
                raise

        # Also delete the keys secret
        await self.delete_secret(tenant_name, f"{agent_name}-keys")
        return {"status": "deleted", "name": agent_name}

    # ─── Pod Status ───

    async def get_pod_status(self, tenant_name: str, agent_name: str) -> dict:
        """Get pod status for agent"""
        await self.initialize()
        try:
            pods = await self._core_v1.list_namespaced_pod(
                namespace=f"tenant-{tenant_name}",
                label_selector=f"app.kubernetes.io/instance={agent_name},app.kubernetes.io/name=openclaw",
            )
            if not pods.items:
                return {"status": "not_found", "phase": None}

            pod = pods.items[0]
            container_statuses = []
            for cs in pod.status.container_statuses or []:
                container_statuses.append({
                    "name": cs.name,
                    "ready": cs.ready,
                    "restart_count": cs.restart_count,
                    "state": "running" if cs.state.running else "waiting" if cs.state.waiting else "terminated",
                })

            return {
                "status": "found",
                "phase": pod.status.phase,
                "pod_name": pod.metadata.name,
                "node": pod.spec.node_name,
                "start_time": pod.status.start_time.isoformat() if pod.status.start_time else None,
                "containers": container_statuses,
                "conditions": [
                    {"type": c.type, "status": c.status}
                    for c in pod.status.conditions or []
                ],
            }
        except client.exceptions.ApiException as e:
            if e.status == 404:
                return {"status": "not_found", "phase": None}
            raise

    async def get_pod_logs(
        self,
        tenant_name: str,
        agent_name: str,
        container: str = "openclaw",
        tail_lines: int = 100,
    ) -> dict:
        """Get pod logs for an agent container"""
        await self.initialize()
        try:
            pods = await self._core_v1.list_namespaced_pod(
                namespace=f"tenant-{tenant_name}",
                label_selector=f"app.kubernetes.io/instance={agent_name},app.kubernetes.io/name=openclaw",
            )
            if not pods.items:
                return {"error": "Pod not found", "logs": ""}

            pod = pods.items[0]
            pod_name = pod.metadata.name
            namespace = f"tenant-{tenant_name}"

            # Get available containers
            containers = [
                cs.name for cs in (pod.status.container_statuses or [])
            ]

            if container not in containers:
                return {
                    "error": f"Container '{container}' not found",
                    "available_containers": containers,
                    "logs": "",
                }

            logs = await self._core_v1.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container=container,
                tail_lines=tail_lines,
            )
            return {
                "pod_name": pod_name,
                "container": container,
                "available_containers": containers,
                "tail_lines": tail_lines,
                "logs": logs,
            }
        except client.exceptions.ApiException as e:
            if e.status == 404:
                return {"error": "Pod not found", "logs": ""}
            raise


# Global instance
k8s_client = K8sClient()