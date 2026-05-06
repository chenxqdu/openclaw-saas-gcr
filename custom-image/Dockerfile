# OpenClaw Custom Image (workshop)
# Pre-installs tools that cannot be fetched via npx at runtime (China firewall).
# Base: mirror of openclaw upstream (user=node uid 1000, HOME=/home/node).
# Target: linux/arm64 (Graviton).
#
# Build:
#   docker buildx build --platform linux/arm64 \
#     --build-arg BASE_REGISTRY=public.ecr.aws/i4x4j7g8/openclaw-saas \
#     --build-arg BASE_TAG=2026.4.14 \
#     -t <registry>/openclaw-custom:2026.4.14 --push .
ARG BASE_REGISTRY=public.ecr.aws/i4x4j7g8/openclaw-saas
ARG BASE_TAG=2026.4.14
FROM ${BASE_REGISTRY}/openclaw/openclaw:${BASE_TAG}

USER root

# ── System deps ──
RUN apt-get update && apt-get install -y --no-install-recommends jq \
    && rm -rf /var/lib/apt/lists/*

# ── Pre-install acpx into extension dir ──
# Base image has /app/extensions/acpx/package.json but no node_modules.
# Strip workspace:* devDependencies so npm install won't fail on unresolved workspace refs,
# then install the pinned acpx version declared in dependencies.
RUN cd /app/extensions/acpx \
    && PINNED=$(node -e "console.log(JSON.parse(require('fs').readFileSync('package.json','utf8')).dependencies.acpx)") \
    && echo "Installing acpx@${PINNED}" \
    && node -e "const p=JSON.parse(require('fs').readFileSync('package.json','utf8')); delete p.devDependencies; require('fs').writeFileSync('package.json',JSON.stringify(p,null,2))" \
    && npm install --omit=dev --no-save "acpx@${PINNED}" 2>&1 \
    && /app/extensions/acpx/node_modules/.bin/acpx --version

# ── Pre-install ACP agent binary (CN cannot use npx to download at runtime) ──
RUN npm install -g @zed-industries/claude-agent-acp@0.21.0

# ── Kiro CLI (native binary, headless, aarch64) ──
ARG KIRO_VERSION=1.28.0
RUN curl -fsSL "https://desktop-release.q.us-east-1.amazonaws.com/${KIRO_VERSION}/kirocli-aarch64-linux.tar.gz" \
    | tar xz -C /tmp \
    && cp /tmp/kirocli/bin/kiro-cli /usr/local/bin/ \
    && cp /tmp/kirocli/bin/kiro-cli-chat /usr/local/bin/ \
    && chmod +x /usr/local/bin/kiro-cli /usr/local/bin/kiro-cli-chat \
    && rm -rf /tmp/kirocli

# ── Kiro agent config ──
# Two copies:
#   1. /home/node/.kiro/ — image layer, used in non-persistent fallback mode
#   2. /opt/kiro-config/ — image layer, source for persistent mode
#
# For persistent kiro sessions, the .acpxrc.json agent command sets
# HOME=~/.openclaw (PVC) and copies config from /opt/kiro-config/ on first run.
RUN mkdir -p /home/node/.kiro/agents /home/node/.kiro/settings \
    && mkdir -p /opt/kiro-config/agents /opt/kiro-config/settings
COPY kiro-agent-config.json /home/node/.kiro/agents/kiro_default.json
COPY kiro-agent-config.json /opt/kiro-config/agents/kiro_default.json
RUN echo '{"chat.defaultAgent":"kiro_default"}' \
    | tee /home/node/.kiro/settings/cli.json > /opt/kiro-config/settings/cli.json \
    && chown -R 1000:1000 /home/node/.kiro /opt/kiro-config

USER node

# ── Verify ──
RUN kiro-cli --version && jq --version \
    && claude-agent-acp --version \
    && /app/extensions/acpx/node_modules/.bin/acpx --version

LABEL org.opencontainers.image.title="openclaw-custom" \
      org.opencontainers.image.description="OpenClaw with pre-installed acpx + claude-agent-acp + kiro-cli" \
      org.opencontainers.image.source="https://github.com/chenxqdu/openclaw-saas-gcr"
