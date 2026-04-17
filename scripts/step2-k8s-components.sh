#!/bin/bash
set -euo pipefail

########################################
# OpenClaw CN Workshop - Step 2
# K8s Components Deployment Script
# Run this on the IDE instance after Step 1 completes.
########################################

STACK_NAME="${STACK_NAME:-openclaw-cn-workshop}"
REGION="${REGION:-cn-northwest-1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "============================================"
echo "  OpenClaw Workshop - Step 2: K8s Components"
echo "  Stack: $STACK_NAME | Region: $REGION"
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

# 2. ALB Controller (OCI chart from public.ecr.aws — no GitHub repo access needed)
ALB_CHART="${ALB_CHART:-oci://public.ecr.aws/h4t8a9b8/openclaw-saas/charts/aws-load-balancer-controller}"
ALB_VERSION="${ALB_VERSION:-3.1.0}"

echo ""
echo ">>> [2/5] Installing ALB Controller..."
helm upgrade --install aws-load-balancer-controller \
  "$ALB_CHART" \
  --namespace kube-system \
  --version "$ALB_VERSION" \
  --set clusterName="$CLUSTER_NAME" \
  --set serviceAccount.create=true \
  --set serviceAccount.name=aws-load-balancer-controller \
  --set "serviceAccount.annotations.eks\.amazonaws\.com/role-arn=$ALB_CONTROLLER_ROLE_ARN" \
  --set region="$REGION" \
  --set vpcId="$VPC_ID" \
  --wait --timeout 5m

# 3. StorageClasses
echo ""
echo ">>> [3/5] Creating StorageClasses..."
cat "$SCRIPT_DIR/../yaml/storage-classes.yaml" | \
  sed "s/\${EFS_FILE_SYSTEM_ID}/$EFS_FILE_SYSTEM_ID/g" | \
  kubectl apply --server-side --force-conflicts -f -

# 4. OpenClaw Operator CRD + Deployment (static yaml, images from public.ecr.aws)
echo ""
echo ">>> [4/5] Applying OpenClaw CRDs..."
echo "  Applying OpenClawInstance CRD (large file, may take a moment)..."
kubectl apply --server-side --force-conflicts --timeout=120s -f "$SCRIPT_DIR/../yaml/openclaw-crd.yaml"
kubectl apply --server-side --force-conflicts -f "$SCRIPT_DIR/../yaml/openclaw-selfconfig-crd.yaml"
# Verify both CRDs exist
echo "  Verifying CRDs..."
kubectl get crd openclawinstances.openclaw.rocks openclawselfconfigs.openclaw.rocks

echo ""
echo ">>> [5/5] Deploying OpenClaw Operator..."
kubectl create namespace openclaw-operator-system 2>/dev/null || true
kubectl apply --server-side --force-conflicts -n openclaw-operator-system -f "$SCRIPT_DIR/../yaml/openclaw-operator.yaml"

echo ""
echo ">>> Waiting for operator to be ready..."
kubectl rollout status deployment/openclaw-operator -n openclaw-operator-system --timeout=120s

# 6. Pre-pull & retag images that operator hardcodes (nginx, uv)
# CN cannot reach docker.io or ghcr.io. We pull from public.ecr.aws and
# tag them as the names the operator expects, so kubelet finds them locally.
echo ""
echo ">>> [6/6] Pre-pulling operator-injected images on all nodes..."
cat <<'RETAG_EOF' | kubectl apply -f -
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
        image: public.ecr.aws/h4t8a9b8/openclaw-saas/busybox:1.37.0
        securityContext:
          privileged: true
        command: ["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "--", "sh", "-c"]
        args:
        - |
          echo "=== Pulling and retagging images ==="
          # nginx (operator injects as docker.io/library/nginx:1.27-alpine)
          ctr -n k8s.io images pull public.ecr.aws/h4t8a9b8/openclaw-saas/nginx:1.27-alpine 2>&1
          ctr -n k8s.io images tag --force public.ecr.aws/h4t8a9b8/openclaw-saas/nginx:1.27-alpine docker.io/library/nginx:1.27-alpine 2>&1
          echo "nginx done"
          # opentelemetry-collector
          ctr -n k8s.io images pull public.ecr.aws/h4t8a9b8/openclaw-saas/opentelemetry-collector:0.120.0 2>&1                              
          ctr -n k8s.io images tag --force public.ecr.aws/h4t8a9b8/openclaw-saas/opentelemetry-collector:0.120.0 docker.io/otel/opentelemetry-collector:0.120.0 2>&1
          echo "opentelemetry-collector done"
          # uv (operator injects as ghcr.io/astral-sh/uv:0.6-bookworm-slim)
          ctr -n k8s.io images pull public.ecr.aws/h4t8a9b8/openclaw-saas/uv:0.6-bookworm-slim 2>&1
          ctr -n k8s.io images tag --force public.ecr.aws/h4t8a9b8/openclaw-saas/uv:0.6-bookworm-slim ghcr.io/astral-sh/uv:0.6-bookworm-slim 2>&1
          echo "uv done"
          # tailscale (operator injects as ghcr.io/tailscale/tailscale if enabled)
          ctr -n k8s.io images pull public.ecr.aws/h4t8a9b8/openclaw-saas/tailscale:2026.03.18 2>&1
          ctr -n k8s.io images tag --force public.ecr.aws/h4t8a9b8/openclaw-saas/tailscale:2026.03.18 ghcr.io/tailscale/tailscale:latest 2>&1
          echo "tailscale done"
          # metrics-exporter (platform API code generates image name as openclaw-saas-dev-metrics-exporter)
          ctr -n k8s.io images pull public.ecr.aws/h4t8a9b8/openclaw-saas/openclaw-saas-dev-metrics-exporter:v0.1.0 2>&1
          echo "metrics-exporter done"

          # rclone image 
          ctr -n k8s.io images pull public.ecr.aws/h4t8a9b8/openclaw-saas/rclone:1.68 2>&1
          ctr -n k8s.io images tag --force public.ecr.aws/h4t8a9b8/openclaw-saas/rclone:1.68 docker.io/rclone/rclone:1.68 2>&1
          echo "openclaw-custom done"
          # chromium sidecar (CRD default uses public.ecr.aws, also retag for ghcr.io fallback)
          ctr -n k8s.io images pull public.ecr.aws/h4t8a9b8/openclaw-saas/chromium:2026.03.17 2>&1
          ctr -n k8s.io images tag --force public.ecr.aws/h4t8a9b8/openclaw-saas/chromium:2026.03.17 ghcr.io/browserless/chromium:latest 2>&1
          echo "chromium done"
          # billing-consumer (SQS consumer + aggregator)
          ctr -n k8s.io images pull public.ecr.aws/h4t8a9b8/openclaw-saas/billing-consumer:v0.1.0 2>&1
          echo "billing-consumer done"
          echo "=== All images ready ==="
          sleep 3600
RETAG_EOF

echo "  Waiting for DaemonSet pods to be ready..."
kubectl rollout status daemonset/cn-image-prepull -n kube-system --timeout=120s

# Wait for retag to finish (check logs)
sleep 10
echo "  Checking retag results..."
for pod in $(kubectl get pods -n kube-system -l app=cn-image-prepull -o name); do
  echo "--- $pod ---"
  kubectl logs -n kube-system "$pod" --tail=5
done

echo ""
echo "============================================"
echo "  Step 2 Complete!"
echo "============================================"
kubectl get pods -A | grep -E "efs|alb|openclaw"
kubectl get sc
kubectl get crd | grep openclaw