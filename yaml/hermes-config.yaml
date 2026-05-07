apiVersion: v1
kind: ConfigMap
metadata:
  name: hermes-config
  namespace: hermes
data:
  config.yaml: |
    model:
      default: "${HERMES_MODEL}"
      provider: "custom"
      base_url: "${LITELLM_BASE_URL}/v1"
    terminal:
      backend: "local"
      cwd: "/mnt/workspace"
      timeout: 180
    agent:
      max_turns: 60
    memory:
      memory_enabled: true
      user_profile_enabled: true
    display:
      streaming: true
