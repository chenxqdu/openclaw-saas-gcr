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

# 1. EFS CSI Driver
echo ""
echo ">>> [1/5] Installing EFS CSI Driver..."
helm repo add aws-efs-csi-driver https://kubernetes-sigs.github.io/aws-efs-csi-driver/ 2>/dev/null || true
helm repo update
helm upgrade --install aws-efs-csi-driver \
  aws-efs-csi-driver/aws-efs-csi-driver \
  --namespace kube-system \
  --version 3.4.1 \
  --set controller.serviceAccount.create=true \
  --set controller.serviceAccount.name=efs-csi-controller-sa \
  --wait --timeout 5m

# EFS CSI Pod Identity
echo ">>> Creating EFS CSI Pod Identity Association..."
aws eks create-pod-identity-association \
  --cluster-name "$CLUSTER_NAME" \
  --namespace kube-system \
  --service-account efs-csi-controller-sa \
  --role-arn "$EFS_CSI_DRIVER_ROLE_ARN" \
  --region "$REGION" 2>/dev/null || echo "  (already exists)"

# 2. ALB Controller
echo ""
echo ">>> [2/5] Installing ALB Controller..."
helm repo add eks https://aws.github.io/eks-charts 2>/dev/null || true
helm repo update
helm upgrade --install aws-load-balancer-controller \
  eks/aws-load-balancer-controller \
  --namespace kube-system \
  --version 3.1.0 \
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

# 4. OpenClaw Operator CRD
echo ""
echo ">>> [4/5] Applying OpenClaw CRD..."
kubectl apply --server-side --force-conflicts -f "$SCRIPT_DIR/../yaml/openclaw-crd.yaml"

# 5. OpenClaw Operator
echo ""
echo ">>> [5/5] Deploying OpenClaw Operator..."
kubectl apply --server-side --force-conflicts -f "$SCRIPT_DIR/../yaml/openclaw-operator.yaml"

echo ""
echo ">>> Waiting for operator to be ready..."
kubectl rollout status deployment/openclaw-operator -n openclaw-operator-system --timeout=120s

echo ""
echo "============================================"
echo "  Step 2 Complete!"
echo "============================================"
kubectl get pods -A | grep -E "efs|alb|openclaw"
kubectl get sc
kubectl get crd | grep openclaw
