"""Channel management service - builds CRD-compatible channel configs"""
from typing import Dict, List

from api.config import settings

# Channel definitions: required credentials + how to build openclaw.json config
CHANNEL_DEFINITIONS = {
    "telegram": {
        "required": ["bot_token"],
        "optional": [],
        "build": lambda creds: {
            "enabled": True,
            "accounts": {
                "default": {
                    "botToken": creds["bot_token"],
                }
            },
        },
    },
    "feishu": {
        "required": ["app_id", "app_secret"],
        "optional": [],
        "build": lambda creds: {
            "enabled": True,
            "accounts": {
                "default": {
                    "appId": creds["app_id"],
                    "appSecret": creds["app_secret"],
                }
            },
        },
    },
    "discord": {
        "required": ["bot_token"],
        "optional": ["application_id"],
        "build": lambda creds: {
            "enabled": True,
            "accounts": {
                "default": {
                    k: v for k, v in {
                        "botToken": creds["bot_token"],
                        "applicationId": creds.get("application_id"),
                    }.items() if v is not None
                }
            },
        },
    },
    "whatsapp": {
        "required": ["phone_number_id", "access_token", "verify_token"],
        "optional": [],
        "build": lambda creds: {
            "enabled": True,
            "accounts": {
                "default": {
                    "phoneNumberId": creds["phone_number_id"],
                    "accessToken": creds["access_token"],
                    "verifyToken": creds["verify_token"],
                }
            },
        },
    },
    "wecom": {
        "required": ["bot_id", "secret"],
        "optional": [],
        "build": lambda creds: {
            "enabled": True,
            "connectionMode": "websocket",
            "botId": creds["bot_id"],
            "secret": creds["secret"],
        },
    },
}


def get_supported_channels() -> List[str]:
    """Get list of supported channel types for this region"""
    allowed = [ch.strip() for ch in settings.AVAILABLE_CHANNELS.split(",") if ch.strip()]
    if allowed:
        return [ch for ch in CHANNEL_DEFINITIONS if ch in allowed]
    return list(CHANNEL_DEFINITIONS.keys())


def validate_channel_credentials(channel_type: str, credentials: Dict[str, str]) -> None:
    """Validate that all required credentials are provided"""
    supported = get_supported_channels()
    if channel_type not in supported:
        raise ValueError(f"Unsupported channel type: {channel_type}. Supported in this region: {', '.join(supported)}")

    defn = CHANNEL_DEFINITIONS[channel_type]
    required = set(defn["required"])
    provided = set(credentials.keys())
    missing = required - provided
    if missing:
        raise ValueError(f"Missing required credentials for {channel_type}: {', '.join(missing)}")


def build_channel_config(channel_type: str, credentials: Dict[str, str]) -> Dict:
    """Build openclaw.json-compatible channel config"""
    validate_channel_credentials(channel_type, credentials)
    return CHANNEL_DEFINITIONS[channel_type]["build"](credentials)


def build_crd_channel_patch(agent_name: str, channel_type: str, credentials: Dict[str, str]) -> dict:
    """Build a CRD patch to add a channel configuration.

    OpenClaw requires:
    - dmPolicy: "open" + allowFrom: ["*"] to skip device pairing
    - groupPolicy: "open" + groupAllowFrom: ["*"] for group messages
    """
    channel_conf = build_channel_config(channel_type, credentials)
    # Add open policy so users can chat without pairing
    channel_conf["dmPolicy"] = "open"
    channel_conf["allowFrom"] = ["*"]
    channel_conf["groupPolicy"] = "open"
    channel_conf["groupAllowFrom"] = ["*"]
    return {
        "spec": {
            "config": {
                "mergeMode": "merge",
                "raw": {
                    "channels": {
                        channel_type: channel_conf,
                    }
                },
            }
        }
    }


def build_crd_channel_remove_patch(channel_type: str) -> dict:
    """Build a CRD patch to disable a channel"""
    return {
        "spec": {
            "config": {
                "mergeMode": "merge",
                "raw": {
                    "channels": {
                        channel_type: {
                            "enabled": False,
                        }
                    }
                },
            }
        }
    }


def build_secret_name(agent_name: str, channel_type: str) -> str:
    """Build Kubernetes secret name for channel (legacy, kept for compat)"""
    return f"{agent_name}-{channel_type}-credentials"
