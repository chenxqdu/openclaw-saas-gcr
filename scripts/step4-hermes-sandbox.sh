#!/bin/bash
set -euo pipefail

########################################
# OpenClaw CN Workshop - Step 4 (default: static nodegroup)
# Hermes Agent Sandbox Deployment
#
# Installs the kubernetes-sigs agent-sandbox controller and deploys
# the Hermes agent (https://github.com/NousResearch/hermes-agent) as
# a Sandbox CRD instance. Hermes talks to an externally-hosted
# LiteLLM endpoint for model routing.
#
# Hermes pods land on the dedicated `sandbox-nodes` managed nodegroup
# provisioned by Step 1 CFN (labels: workload-type=sandbox;
# taints: sandbox=true:NoSchedule). No Karpenter, no runtime EC2
# provisioning — works in accounts whose Organizations SCP forbids
# ec2:CreateLaunchTemplate.
#
# If you DO want on-demand scaling via Karpenter, use the variant
# script scripts/step4-hermes-sandbox-karpenter.sh instead. Only
# one variant should be active at a time.
#
# Runs after Step 1/2/3. Independent of the platform-managed OpenClaw
# path — Hermes lives in its own namespace and does NOT touch
# operator/platform-api resources.
########################################

STACK_NAME="${STACK_NAME:-openclaw-cn-workshop}"
REGION="${REGION:-cn-northwest-1}"

# Image registry — same convention as step2/step3.
ECR_REGISTRY="${ECR_REGISTRY:-public.ecr.aws/i4x4j7g8/openclaw-saas}"

# agent-sandbox version (yaml/agent-sandbox-*.yaml are vendored at this version).
AGENT_SANDBOX_VERSION="${AGENT_SANDBOX_VERSION:-v0.3.10}"

# Hermes agent image (mirrored from docker.io/nousresearch/hermes-agent).
HERMES_IMAGE="${HERMES_IMAGE:-${ECR_REGISTRY}/nousresearch/hermes-agent:latest}"

# LiteLLM proxy endpoint (user-hosted). base_url form: https://host[/path]
LITELLM_BASE_URL="${LITELLM_BASE_URL:?must set LITELLM_BASE_URL, e.g. https://litellm.example.com}"
LITELLM_API_KEY="${LITELLM_API_KEY:?must set LITELLM_API_KEY (Bearer token)}"
HERMES_MODEL="${HERMES_MODEL:-zai-org/glm-5}"

# Feishu credentials — bot must be a separate Feishu app from the
# OpenClaw feishu bot used in Step 3 (a Feishu bot can't be shared
# between two websocket consumers).
FEISHU_APP_ID="${FEISHU_APP_ID:?must set FEISHU_APP_ID (cli_*) for Hermes bot}"
FEISHU_APP_SECRET="${FEISHU_APP_SECRET:?must set FEISHU_APP_SECRET for Hermes bot}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
YAML_DIR="${SCRIPT_DIR}/../yaml"

echo "============================================"
echo "  OpenClaw Workshop - Step 4: Hermes Sandbox"
echo "  (static sandbox-nodes managed nodegroup)"
echo "  Stack: $STACK_NAME | Region: $REGION"
echo "  Registry: $ECR_REGISTRY"
echo "  Agent Sandbox: $AGENT_SANDBOX_VERSION"
echo "============================================"

# ---- [1/4] Fetch Step 1 outputs + verify sandbox nodegroup ----
echo ""
echo ">>> [1/4] Fetching Step 1 outputs + verifying sandbox-nodes..."
get_output() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

CLUSTER_NAME=$(get_output ClusterName)
echo "  Cluster: $CLUSTER_NAME"

aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$REGION" >/dev/null

# Confirm the sandbox-nodes managed nodegroup is ACTIVE before
# deploying workloads that require it; fail fast with a clear hint
# if the CFN template is out of date.
SANDBOX_NG_STATUS=$(aws eks describe-nodegroup \
  --cluster-name "$CLUSTER_NAME" \
  --nodegroup-name sandbox-nodes \
  --region "$REGION" \
  --query 'nodegroup.status' --output text 2>/dev/null || echo "MISSING")
if [ "$SANDBOX_NG_STATUS" != "ACTIVE" ]; then
  echo "ERROR: sandbox-nodes managed nodegroup status=$SANDBOX_NG_STATUS."
  echo "       Expected ACTIVE. Run 'aws cloudformation update-stack' with"
  echo "       the latest template — it provisions 'sandbox-nodes' with the"
  echo "       required workload-type=sandbox label + sandbox=true NoSchedule taint."
  exit 1
fi
echo "  sandbox-nodes: ACTIVE"

# ---- [2/4] Install Agent Sandbox controller ----
echo ""
echo ">>> [2/4] Installing Agent Sandbox ${AGENT_SANDBOX_VERSION}..."
# Both manifests carry the controller image as a ${ECR_REGISTRY}
# placeholder (see yaml/agent-sandbox-*.yaml). Upstream ships both —
# extensions.yaml re-declares the same Deployment so we must apply the
# same registry substitution to both, otherwise the second apply
# clobbers the first with the upstream registry.k8s.io reference.
for f in "${YAML_DIR}/agent-sandbox-manifest.yaml" "${YAML_DIR}/agent-sandbox-extensions.yaml"; do
  sed "s|\${ECR_REGISTRY}|${ECR_REGISTRY}|g" "$f" \
    | kubectl apply --server-side --force-conflicts --timeout=120s -f -
done

echo "  Waiting for agent-sandbox-system deployments..."
for d in $(kubectl -n agent-sandbox-system get deploy -o name 2>/dev/null); do
  kubectl -n agent-sandbox-system rollout status "$d" --timeout=180s
done

kubectl get crd | grep agents.x-k8s.io || {
  echo "ERROR: agent-sandbox CRDs not registered"
  exit 1
}

# ---- [3/4] Namespace + secrets ----
echo ""
echo ">>> [3/4] Creating hermes namespace + secrets..."
kubectl create namespace hermes --dry-run=client -o yaml | kubectl apply -f -

kubectl -n hermes create secret generic hermes-litellm-key \
  --from-literal=api-key="$LITELLM_API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n hermes create secret generic hermes-feishu \
  --from-literal=app-id="$FEISHU_APP_ID" \
  --from-literal=app-secret="$FEISHU_APP_SECRET" \
  --dry-run=client -o yaml | kubectl apply -f -

# ---- [4/4] Deploy Hermes Sandbox ----
echo ""
echo ">>> [4/4] Deploying Hermes Sandbox..."
sed -e "s|\${HERMES_MODEL}|${HERMES_MODEL}|g" \
    -e "s|\${LITELLM_BASE_URL}|${LITELLM_BASE_URL}|g" \
    "${YAML_DIR}/hermes-config.yaml" | kubectl apply -f -

sed -e "s|\${HERMES_IMAGE}|${HERMES_IMAGE}|g" \
    "${YAML_DIR}/hermes-sandbox.yaml" | kubectl apply -f -

echo ""
echo "============================================"
echo "  Step 4 Complete!"
echo "============================================"
echo ""
echo ">>> agent-sandbox controller:"
kubectl -n agent-sandbox-system get pods 2>/dev/null | head -5
echo ""
echo ">>> Hermes Sandbox status:"
kubectl -n hermes get sandbox,pod 2>&1 | head -10
echo ""
echo ">>> Follow pod startup:"
echo "  kubectl -n hermes get pod -l sandbox=hermes-feishu-sandbox -w"
echo ""
echo ">>> Tail logs once running (expect: [Lark] connected to wss://msg-frontier.feishu.cn):"
echo "  kubectl -n hermes logs -l sandbox=hermes-feishu-sandbox -f --tail=50"
