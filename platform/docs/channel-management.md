# Channel Management

Channels (Telegram, Feishu, Discord, WhatsApp) connect an OpenClaw agent to messaging platforms.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/tenants/{tenant}/agents/{id}/channels` | Bind or re-bind a channel |
| `PUT` | `/api/v1/tenants/{tenant}/agents/{id}/channels/{type}` | Update channel credentials |
| `DELETE` | `/api/v1/tenants/{tenant}/agents/{id}/channels/{type}` | Unbind a channel |

## What Happens When You Bind/Update a Channel

1. **API validates credentials** — checks required fields for the channel type
2. **CRD patch** — the Platform API patches the `OpenClawInstance` CRD:
   ```yaml
   spec:
     config:
       mergeMode: merge
       raw:
         channels:
           telegram:  # or feishu, discord, whatsapp
             enabled: true
             dmPolicy: open
             allowFrom: ["*"]
             groupPolicy: open
             groupAllowFrom: ["*"]
             accounts:
               default:
                 botToken: "..."
   ```
3. **Operator detects change** — the openclaw-operator watches CRD changes and reconciles:
   - Updates the agent's `ConfigMap` with the new channel config
   - Recomputes the `openclaw.rocks/config-hash` annotation
4. **Pod auto-restarts** — because the config-hash annotation changed, Kubernetes triggers a rolling restart of the agent's StatefulSet pod
5. **Agent comes up with new channel** — the OpenClaw gateway inside the pod reads the updated config and connects to the messaging platform

**No manual restart is needed.** The entire flow is automatic.

## Typical Timing

- API call → CRD patch: **< 1 second**
- Operator reconcile: **~5 seconds**
- Pod restart + gateway ready: **30-60 seconds**

Total: ~1 minute from API call to the agent being reachable on the new channel.

## Bind vs Update vs Re-bind

- **Bind (POST)**: Adds a new channel. If the channel is already bound, it will **re-bind** (update credentials) instead of rejecting.
- **Update (PUT)**: Explicitly updates credentials for an existing channel. Returns 404 if the channel isn't bound.
- **Unbind (DELETE)**: Disables the channel (sets `enabled: false` in CRD) and removes it from the agent's channel list.

## Required Credentials by Channel

| Channel | Required Fields |
|---------|----------------|
| `telegram` | `bot_token` |
| `feishu` | `app_id`, `app_secret` |
| `discord` | `bot_token` (optional: `application_id`) |
| `whatsapp` | `phone_number_id`, `access_token`, `verify_token` |

## Example: Bind Telegram

```bash
curl -X POST https://openclaw.chenxqdu.space/api/v1/tenants/my-tenant/agents/1/channels \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "channel_type": "telegram",
    "credentials": {
      "bot_token": "123456:ABC-DEF..."
    }
  }'
```

## Example: Update Telegram Token

```bash
curl -X PUT https://openclaw.chenxqdu.space/api/v1/tenants/my-tenant/agents/1/channels/telegram \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "channel_type": "telegram",
    "credentials": {
      "bot_token": "NEW_TOKEN_HERE"
    }
  }'
```

## Troubleshooting

- **Channel not responding after bind**: Check pod status (`kubectl get pods -n tenant-{name}`). The pod should restart within ~60s.
- **"Tenant not found" error**: Ensure you have at least `member` role on the tenant (owner, platform-admin, or member).
- **Pod stuck in CrashLoopBackOff after channel bind**: Usually means invalid credentials. Check gateway logs: `kubectl logs -n tenant-{name} {agent}-0 -c openclaw`.
