#!/bin/bash
set -euo pipefail

########################################
# OpenClaw CN Workshop - Step 4
# Hermes Agent Sandbox Deployment
#
# Adds a Karpenter-managed "sandbox" nodepool and deploys the Hermes
# agent (https://github.com/NousResearch/hermes-agent) as a Sandbox
# CRD instance (https://agent-sandbox.sigs.k8s.io). Hermes talks to
# an externally-hosted LiteLLM endpoint for model routing.
#
# Runs after Step 1/2/3. Independent of the platform-managed OpenClaw
# path — Hermes lives in its own namespace with its own Karpenter
# nodepool and does NOT touch operator/platform-api resources.
########################################

STACK_NAME="${STACK_NAME:-openclaw-cn-workshop}"
REGION="${REGION:-cn-northwest-1}"

# Image registry — same convention as step2/step3.
ECR_REGISTRY="${ECR_REGISTRY:-public.ecr.aws/i4x4j7g8/openclaw-saas}"

# Karpenter + agent-sandbox versions.
KARPENTER_VERSION="${KARPENTER_VERSION:-1.9.0}"
AGENT_SANDBOX_VERSION="${AGENT_SANDBOX_VERSION:-v0.3.10}"

# Hermes agent image (mirrored from docker.io/nousresearch/hermes-agent).
HERMES_IMAGE="${HERMES_IMAGE:-${ECR_REGISTRY}/nousresearch/hermes-agent:latest}"

# Agent-sandbox controller image — upstream publishes to registry.k8s.io
# which is unreliable from CN nodes. We mirror it and rewrite the
# manifest.yaml on apply.
AGENT_SANDBOX_CONTROLLER_IMAGE="${AGENT_SANDBOX_CONTROLLER_IMAGE:-${ECR_REGISTRY}/agent-sandbox/agent-sandbox-controller:${AGENT_SANDBOX_VERSION}}"

# LiteLLM proxy endpoint (user-hosted). base_url form: https://host[/path]
LITELLM_BASE_URL="${LITELLM_BASE_URL:?must set LITELLM_BASE_URL, e.g. https://litellm.example.com}"
LITELLM_API_KEY="${LITELLM_API_KEY:?must set LITELLM_API_KEY (Bearer token)}"
HERMES_MODEL="${HERMES_MODEL:-claude-sonnet-4-5}"

# Feishu credentials — bot must be a separate Feishu app from the
# OpenClaw feishu bot used in Step 3 (a Feishu bot can't be shared
# between two websocket consumers).
FEISHU_APP_ID="${FEISHU_APP_ID:?must set FEISHU_APP_ID (cli_*) for Hermes bot}"
FEISHU_APP_SECRET="${FEISHU_APP_SECRET:?must set FEISHU_APP_SECRET for Hermes bot}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
YAML_DIR="${SCRIPT_DIR}/../yaml"

echo "============================================"
echo "  OpenClaw Workshop - Step 4: Hermes Sandbox"
echo "  Stack: $STACK_NAME | Region: $REGION"
echo "  Registry: $ECR_REGISTRY"
echo "  Karpenter: $KARPENTER_VERSION"
echo "  Agent Sandbox: $AGENT_SANDBOX_VERSION"
echo "============================================"

# ---- [1/5] Fetch Step 1 outputs ----
echo ""
echo ">>> [1/5] Fetching Step 1 outputs..."
get_output() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

CLUSTER_NAME=$(get_output ClusterName)
KARPENTER_CONTROLLER_ROLE_ARN=$(get_output KarpenterControllerRoleArn)
KARPENTER_NODE_ROLE_ARN=$(get_output KarpenterNodeRoleArn)
KARPENTER_INTERRUPTION_QUEUE=$(get_output KarpenterInterruptionQueueName)

# Derive node role name from ARN (KarpenterNodeRole is a managed IAM
# role, not an instance profile; EC2NodeClass.spec.role takes the
# role name, not the ARN or instance profile name).
KARPENTER_NODE_ROLE_NAME="${KARPENTER_NODE_ROLE_ARN##*/}"

echo "  Cluster: $CLUSTER_NAME"
echo "  Karpenter controller role: $KARPENTER_CONTROLLER_ROLE_ARN"
echo "  Karpenter node role: $KARPENTER_NODE_ROLE_NAME"
echo "  Interruption queue: $KARPENTER_INTERRUPTION_QUEUE"

# Ensure kubeconfig points at the workshop cluster.
aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$REGION" >/dev/null

# EC2NodeClass selects the KarpenterNodeSecurityGroup by its
# karpenter.sh/discovery tag, which CFN provisions in Step 1.
# If the tag is missing, the CFN template is out of date —
# fail fast rather than quietly falling back to a wrong SG.
KARPENTER_NODE_SG=$(aws ec2 describe-security-groups --region "$REGION" \
  --filters "Name=tag:karpenter.sh/discovery,Values=${CLUSTER_NAME}" "Name=vpc-id,Values=$(
    aws eks describe-cluster --name "$CLUSTER_NAME" --region "$REGION" --query 'cluster.resourcesVpcConfig.vpcId' --output text
  )" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "")
if [ -z "$KARPENTER_NODE_SG" ] || [ "$KARPENTER_NODE_SG" = "None" ]; then
  echo "ERROR: no security group with tag karpenter.sh/discovery=${CLUSTER_NAME} found."
  echo "       CFN stack ${STACK_NAME} must declare KarpenterNodeSecurityGroup."
  echo "       Run 'aws cloudformation update-stack' with the latest template and retry."
  exit 1
fi
echo "  Karpenter node SG (discovery target): $KARPENTER_NODE_SG"

# ---- [2/5] Install Karpenter (Helm, OCI chart) ----
echo ""
echo ">>> [2/5] Installing Karpenter ${KARPENTER_VERSION}..."
helm upgrade --install karpenter \
  "oci://public.ecr.aws/karpenter/karpenter" \
  --version "$KARPENTER_VERSION" \
  --namespace kube-system \
  --set "settings.clusterName=$CLUSTER_NAME" \
  --set "settings.interruptionQueue=$KARPENTER_INTERRUPTION_QUEUE" \
  --set "serviceAccount.annotations.eks\.amazonaws\.com/role-arn=$KARPENTER_CONTROLLER_ROLE_ARN" \
  --set controller.resources.requests.cpu=200m \
  --set controller.resources.requests.memory=256Mi \
  --set controller.resources.limits.cpu=1 \
  --set controller.resources.limits.memory=1Gi \
  --wait --timeout 5m

# ---- [3/5] Sandbox NodePool + EC2NodeClass ----
echo ""
echo ">>> [3/5] Creating sandbox NodePool + EC2NodeClass..."
cat <<NODEPOOL_EOF | kubectl apply --server-side --force-conflicts -f -
---
apiVersion: karpenter.k8s.aws/v1
kind: EC2NodeClass
metadata:
  name: sandbox
spec:
  amiFamily: AL2023
  role: ${KARPENTER_NODE_ROLE_NAME}
  subnetSelectorTerms:
    - tags:
        karpenter.sh/discovery: ${CLUSTER_NAME}
  securityGroupSelectorTerms:
    - tags:
        karpenter.sh/discovery: ${CLUSTER_NAME}
  amiSelectorTerms:
    - alias: al2023@latest
  tags:
    openclaw-workshop/nodepool: sandbox
    karpenter.sh/discovery: ${CLUSTER_NAME}
---
apiVersion: karpenter.sh/v1
kind: NodePool
metadata:
  name: sandbox
spec:
  template:
    metadata:
      labels:
        workload-type: sandbox
    spec:
      nodeClassRef:
        group: karpenter.k8s.aws
        kind: EC2NodeClass
        name: sandbox
      taints:
        - key: sandbox
          value: "true"
          effect: NoSchedule
      requirements:
        - key: kubernetes.io/arch
          operator: In
          values: ["arm64"]
        - key: kubernetes.io/os
          operator: In
          values: ["linux"]
        - key: node.kubernetes.io/instance-type
          operator: In
          values: ["m6g.large", "m6g.xlarge", "m7g.large", "m7g.xlarge"]
        - key: karpenter.sh/capacity-type
          operator: In
          values: ["on-demand"]
      expireAfter: 168h
  disruption:
    consolidationPolicy: WhenEmpty
    consolidateAfter: 1m
  limits:
    cpu: "16"
    memory: 64Gi
NODEPOOL_EOF

# ---- [4/5] Install Agent Sandbox controller ----
echo ""
echo ">>> [4/5] Installing Agent Sandbox ${AGENT_SANDBOX_VERSION}..."
# The upstream manifest hardcodes registry.k8s.io/agent-sandbox/...
# Rewrite the controller image to our mirror before applying.
upstream_manifest="https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${AGENT_SANDBOX_VERSION}/manifest.yaml"
upstream_ext="https://github.com/kubernetes-sigs/agent-sandbox/releases/download/${AGENT_SANDBOX_VERSION}/extensions.yaml"

# Both manifest.yaml AND extensions.yaml pin the controller image —
# extensions.yaml re-declares the same Deployment with the upstream
# image, so it clobbers manifest.yaml's patched copy if not rewritten.
curl -fsSL "$upstream_manifest" \
  | sed "s|registry.k8s.io/agent-sandbox/agent-sandbox-controller:${AGENT_SANDBOX_VERSION}|${AGENT_SANDBOX_CONTROLLER_IMAGE}|g" \
  | kubectl apply --server-side --force-conflicts --timeout=120s -f -

curl -fsSL "$upstream_ext" \
  | sed "s|registry.k8s.io/agent-sandbox/agent-sandbox-controller:${AGENT_SANDBOX_VERSION}|${AGENT_SANDBOX_CONTROLLER_IMAGE}|g" \
  | kubectl apply --server-side --force-conflicts --timeout=60s -f -

# Wait for the controller deployment (name may vary between releases — just wait
# for any deployment in the agent-sandbox-system namespace to roll out).
echo "  Waiting for agent-sandbox-system deployments..."
for d in $(kubectl -n agent-sandbox-system get deploy -o name 2>/dev/null); do
  kubectl -n agent-sandbox-system rollout status "$d" --timeout=180s
done

kubectl get crd | grep agents.x-k8s.io || {
  echo "ERROR: agent-sandbox CRDs not registered"
  exit 1
}

# ---- [5/5] Deploy Hermes Sandbox ----
echo ""
echo ">>> [5/5] Deploying Hermes Sandbox..."
kubectl create namespace hermes --dry-run=client -o yaml | kubectl apply -f -

# Secrets — credentials never land in YAML.
kubectl -n hermes create secret generic hermes-litellm-key \
  --from-literal=api-key="$LITELLM_API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl -n hermes create secret generic hermes-feishu \
  --from-literal=app-id="$FEISHU_APP_ID" \
  --from-literal=app-secret="$FEISHU_APP_SECRET" \
  --dry-run=client -o yaml | kubectl apply -f -

# ConfigMap and Sandbox CRD — both templated with envsubst.
export HERMES_MODEL LITELLM_BASE_URL HERMES_IMAGE
envsubst < "${YAML_DIR}/hermes-config.yaml.tpl" | kubectl apply -f -
envsubst < "${YAML_DIR}/hermes-sandbox.yaml.tpl" | kubectl apply -f -

echo ""
echo "============================================"
echo "  Step 4 Complete!"
echo "============================================"
echo ""
echo ">>> Karpenter + agent-sandbox controllers:"
kubectl -n kube-system get pods -l app.kubernetes.io/name=karpenter 2>/dev/null | head -5
kubectl -n agent-sandbox-system get pods 2>/dev/null | head -5
echo ""
echo ">>> Hermes Sandbox status:"
kubectl -n hermes get sandbox,pod 2>&1 | head -10
echo ""
echo ">>> Follow pod startup (Ctrl-C to stop watching):"
echo "  kubectl -n hermes get pod -l sandbox=hermes-feishu-sandbox -w"
echo ""
echo ">>> Tail logs once running:"
echo "  kubectl -n hermes logs -l sandbox=hermes-feishu-sandbox -f --tail=50"
echo ""
echo ">>> First pod start may take 3-5 minutes (Karpenter provisioning new EC2 node)."
