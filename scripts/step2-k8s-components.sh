#!/bin/bash
set -euo pipefail

########################################
# OpenClaw CN Workshop - Step 2
# K8s Components Deployment Script
# Run this on the IDE instance after Step 1 completes.
########################################

STACK_NAME="${STACK_NAME:-openclaw-cn-workshop}"
REGION="${REGION:-cn-northwest-1}"

# Image registry used for all pre-pulled images and operator yaml substitution.
# Override via env var to switch to a private mirror. Must match operator
# spec.registry so the rewritten upstream paths (e.g. ghcr.io/openclaw/openclaw
# → ${ECR_REGISTRY}/openclaw/openclaw) land on images kubelet already has.
ECR_REGISTRY="${ECR_REGISTRY:-public.ecr.aws/i4x4j7g8/openclaw-saas}"

# ALB Controller: static yaml rendered from aws.github.io/eks-charts
# v3.2.1 (see yaml/aws-load-balancer-controller.yaml header for refresh
# steps). The CN EKS IDE cannot reach aws.github.io, so installing via
# `helm repo add` at runtime is not reliable. The image itself
# (public.ecr.aws/eks/aws-load-balancer-controller:v3.2.1) IS reachable
# from CN nodes.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "============================================"
echo "  OpenClaw Workshop - Step 2: K8s Components"
echo "  Stack: $STACK_NAME | Region: $REGION"
echo "  Registry: $ECR_REGISTRY"
echo "============================================"

# Get outputs from Step 1
echo ">>> Fetching Step 1 outputs..."
get_output() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

CLUSTER_NAME=$(get_output ClusterName)
VPC_ID=$(get_output VpcId)
EFS_FILE_SYSTEM_ID=$(get_output EFSFileSystemId)
EFS_CSI_DRIVER_ROLE_ARN=$(get_output EFSCSIDriverRoleArn)
ALB_CONTROLLER_ROLE_ARN=$(get_output ALBControllerRoleArn)

echo "  Cluster: $CLUSTER_NAME"
echo "  VPC: $VPC_ID"
echo "  EFS: $EFS_FILE_SYSTEM_ID"

# Setup kubeconfig
echo ">>> Configuring kubectl..."
aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$REGION"

# 1. EFS CSI Driver (via EKS Addon — works in CN without external Helm repos)
echo ""
echo ">>> [1/5] Installing EFS CSI Driver (EKS Addon)..."
aws eks create-addon \
  --cluster-name "$CLUSTER_NAME" \
  --addon-name aws-efs-csi-driver \
  --resolve-conflicts OVERWRITE \
  --pod-identity-associations "[{\"serviceAccount\":\"efs-csi-controller-sa\",\"roleArn\":\"$EFS_CSI_DRIVER_ROLE_ARN\"}]" \
  --region "$REGION" 2>/dev/null || echo "  (already installed or updating)"

echo "  Waiting for addon to be ACTIVE..."
aws eks wait addon-active \
  --cluster-name "$CLUSTER_NAME" \
  --addon-name aws-efs-csi-driver \
  --region "$REGION" 2>&1 || echo "  (wait timed out, checking status...)"
aws eks describe-addon --cluster-name "$CLUSTER_NAME" --addon-name aws-efs-csi-driver \
  --region "$REGION" --query 'addon.{Status:status,Version:addonVersion}' --output table

# 2. ALB Controller (pre-rendered yaml — matches operator / platform yaml style)
echo ""
echo ">>> [2/5] Installing ALB Controller (yaml/aws-load-balancer-controller.yaml)..."
# The yaml bundles the IngressClassParams CRD AND an IngressClassParams
# object in the same file. kubectl caches API discovery per invocation,
# so a single apply registers the CRD server-side but still rejects
# the object locally with "no matches for kind IngressClassParams" —
# the client does not requery discovery mid-apply. Two passes with a
# CRD-established wait in between fixes it deterministically.
RENDERED_ALB="$(mktemp)"
trap 'rm -f "$RENDERED_ALB"' EXIT
sed -e "s|\${CLUSTER_NAME}|$CLUSTER_NAME|g" \
    -e "s|\${ALB_CONTROLLER_ROLE_ARN}|$ALB_CONTROLLER_ROLE_ARN|g" \
    -e "s|\${REGION}|$REGION|g" \
    -e "s|\${VPC_ID}|$VPC_ID|g" \
    "$SCRIPT_DIR/../yaml/aws-load-balancer-controller.yaml" \
  > "$RENDERED_ALB"

# Pass 1: everything except IngressClassParams applies. The one
# expected failure line is filtered out so `set -e` does not abort.
# Plain pipe + `|| true` is enough; no `tee /dev/stderr` because some
# restricted runtimes (EKS IDE pod) expose /dev/stderr as a 400 symlink,
# which makes tee fail with "Permission denied".
echo "  Pass 1/2 (CRDs register; IngressClassParams object is expected to miss on first pass)..."
kubectl apply --server-side --force-conflicts -f "$RENDERED_ALB" 2>&1 \
  | grep -vE 'no matches for kind "IngressClassParams"' \
  || true

# Wait for the ALB CRDs to become Established before re-applying.
for crd in ingressclassparams.elbv2.k8s.aws targetgroupbindings.elbv2.k8s.aws; do
  kubectl wait --for=condition=established --timeout=60s "crd/${crd}"
done

# Pass 2: fresh kubectl invocation requeries discovery; the
# IngressClassParams object now lands. Everything else is a no-op.
echo "  Pass 2/2..."
kubectl apply --server-side --force-conflicts -f "$RENDERED_ALB"

kubectl -n kube-system rollout status deployment/aws-load-balancer-controller --timeout=180s

# 3. StorageClasses
echo ""
echo ">>> [3/5] Creating StorageClasses..."
cat "$SCRIPT_DIR/../yaml/storage-classes.yaml" | \
  sed "s/\${EFS_FILE_SYSTEM_ID}/$EFS_FILE_SYSTEM_ID/g" | \
  kubectl apply --server-side --force-conflicts -f -

# 4. OpenClaw Operator CRD + Deployment
echo ""
echo ">>> [4/5] Applying OpenClaw CRDs..."
echo "  Applying OpenClawInstance CRD (large file, may take a moment)..."
kubectl apply --server-side --force-conflicts --timeout=180s -f "$SCRIPT_DIR/../yaml/openclaw-crd.yaml"
kubectl apply --server-side --force-conflicts -f "$SCRIPT_DIR/../yaml/openclaw-selfconfig-crd.yaml"
# Verify both CRDs exist
echo "  Verifying CRDs..."
kubectl get crd openclawinstances.openclaw.rocks openclawselfconfigs.openclaw.rocks

echo ""
echo ">>> [5/5] Deploying OpenClaw Operator..."
kubectl create namespace openclaw-operator-system 2>/dev/null || true
# -n required: rendered yaml has no metadata.namespace on namespaced kinds,
# so kubectl would default to "default" without this flag.
cat "$SCRIPT_DIR/../yaml/openclaw-operator.yaml" | \
  sed "s|\${ECR_REGISTRY}|$ECR_REGISTRY|g" | \
  kubectl apply --server-side --force-conflicts -n openclaw-operator-system -f -

echo ""
echo ">>> Waiting for operator to be ready..."
kubectl rollout status deployment/openclaw-operator -n openclaw-operator-system --timeout=180s

# 6. Pre-pull images on all nodes to warm the local cache.
# Operator injects spec.registry=${ECR_REGISTRY}, which rewrites upstream image
# paths (ghcr.io/..., docker.io/..., bare names) to ${ECR_REGISTRY}/<path>.
# Pre-pulling those resolved paths means pod startup skips the fetch.
# No retag needed — spec.registry already does the path rewrite.
echo ""
echo ">>> [6/6] Pre-pulling operator-referenced images on all nodes..."
cat <<PREPULL_EOF | kubectl apply -f -
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: cn-image-prepull
  namespace: kube-system
  labels:
    app: cn-image-prepull
spec:
  selector:
    matchLabels:
      app: cn-image-prepull
  template:
    metadata:
      labels:
        app: cn-image-prepull
    spec:
      hostPID: true
      tolerations:
      - operator: Exists
      containers:
      - name: prepull
        image: ${ECR_REGISTRY}/busybox:1.37
        securityContext:
          privileged: true
        command: ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "--", "sh", "-c"]
        args:
        - |
          set -e
          echo "=== Pre-pulling images (registry: ${ECR_REGISTRY}) ==="
          for image in \\
            ${ECR_REGISTRY}/openclaw/openclaw:2026.4.14 \\
            ${ECR_REGISTRY}/openclaw-custom:2026.4.14 \\
            ${ECR_REGISTRY}/astral-sh/uv:0.6-bookworm-slim \\
            ${ECR_REGISTRY}/tailscale/tailscale:latest \\
            ${ECR_REGISTRY}/chromedp/headless-shell:stable \\
            ${ECR_REGISTRY}/nginx:1.27-alpine \\
            ${ECR_REGISTRY}/busybox:1.37 \\
            ${ECR_REGISTRY}/otel/opentelemetry-collector:0.120.0 \\
            ${ECR_REGISTRY}/openclaw-saas-metrics-exporter:v0.3.3 ; do
            echo "-> pulling \$image"
            ctr -n k8s.io images pull "\$image" 2>&1 | tail -1
          done
          echo "=== All 9 images ready ==="
          sleep 3600
PREPULL_EOF

echo "  Waiting for DaemonSet pods to be ready..."
kubectl rollout status daemonset/cn-image-prepull -n kube-system --timeout=120s

# Wait for pulls to finish (check logs)
sleep 10
echo "  Checking pre-pull results..."
for pod in $(kubectl get pods -n kube-system -l app=cn-image-prepull -o name); do
  echo "--- $pod ---"
  kubectl logs -n kube-system "$pod" --tail=12
done

echo ""
echo "============================================"
echo "  Step 2 Complete!"
echo "============================================"
kubectl get pods -A | grep -E "efs|alb|openclaw"
kubectl get sc
kubectl get crd | grep openclaw
