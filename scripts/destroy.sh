#!/bin/bash
set -euo pipefail

########################################
# OpenClaw CN Workshop - Full Destroy
# Tears down Step 3 → Step 2 → Step 1
# in reverse order to avoid dependency issues.
########################################

STACK_NAME="${STACK_NAME:-openclaw-cn-workshop}"
REGION="${REGION:-cn-northwest-1}"
CLUSTER_NAME="${CLUSTER_NAME:-openclaw-cn-workshop}"
DRY_RUN="${DRY_RUN:-false}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}>>>${NC} $*"; }
warn() { echo -e "${YELLOW}⚠️  $*${NC}"; }
err()  { echo -e "${RED}❌  $*${NC}"; }

echo "============================================"
echo -e "  ${RED}OpenClaw Workshop - DESTROY${NC}"
echo "  Stack: $STACK_NAME | Region: $REGION"
echo "============================================"
echo ""

if [ "$DRY_RUN" = "true" ]; then
  warn "DRY RUN mode — no resources will be deleted"
  echo ""
fi

# Confirm
if [ "$DRY_RUN" != "true" ]; then
  echo -e "${RED}⚠️  This will PERMANENTLY DELETE all workshop resources:${NC}"
  echo "  - Platform API (deployment, secrets, NLB)"
  echo "  - OpenClaw Operator + CRD"
  echo "  - Helm charts (EFS CSI, ALB Controller)"
  echo "  - StorageClasses (efs-sc, gp3)"
  echo "  - Pod Identity Associations"
  echo "  - CloudFormation stack (EKS, RDS, SQS, VPC, etc.)"
  echo ""
  read -p "Type 'destroy' to confirm: " confirm
  if [ "$confirm" != "destroy" ]; then
    echo "Aborted."
    exit 1
  fi
  echo ""
fi

# Setup kubeconfig (may fail if cluster already deleted)
log "Configuring kubectl..."
aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$REGION" 2>/dev/null || true

KUBECTL_OK=true
kubectl cluster-info &>/dev/null || KUBECTL_OK=false

########################################
# Step 3: Platform API teardown
########################################
echo ""
echo "============================================"
echo "  Step 3: Removing Platform API"
echo "============================================"

if [ "$KUBECTL_OK" = "true" ]; then
  # Delete NLB service first (takes time to deprovision)
  log "[3.1] Deleting Platform API service (NLB)..."
  if [ "$DRY_RUN" != "true" ]; then
    kubectl delete svc platform-api -n openclaw-platform --ignore-not-found --timeout=60s 2>/dev/null || true
  else
    echo "  (dry-run) kubectl delete svc platform-api -n openclaw-platform"
  fi

  log "[3.2] Deleting Platform API deployment..."
  if [ "$DRY_RUN" != "true" ]; then
    kubectl delete deployment platform-api -n openclaw-platform --ignore-not-found --timeout=60s 2>/dev/null || true
  else
    echo "  (dry-run) kubectl delete deployment platform-api -n openclaw-platform"
  fi

  log "[3.3] Deleting Platform API secrets..."
  if [ "$DRY_RUN" != "true" ]; then
    kubectl delete secret platform-db-secret platform-config platform-admin-seed \
      -n openclaw-platform --ignore-not-found 2>/dev/null || true
  else
    echo "  (dry-run) kubectl delete secrets in openclaw-platform"
  fi

  log "[3.4] Deleting Platform API RBAC..."
  if [ "$DRY_RUN" != "true" ]; then
    kubectl delete clusterrolebinding platform-api-binding --ignore-not-found 2>/dev/null || true
    kubectl delete sa platform-api -n openclaw-platform --ignore-not-found 2>/dev/null || true
  else
    echo "  (dry-run) kubectl delete clusterrolebinding/clusterrole/sa"
  fi

  log "[3.5] Deleting openclaw-platform namespace..."
  if [ "$DRY_RUN" != "true" ]; then
    kubectl delete namespace openclaw-platform --ignore-not-found --timeout=120s 2>/dev/null || true
  else
    echo "  (dry-run) kubectl delete namespace openclaw-platform"
  fi

  # Wait for NLB to deprovision
  log "[3.6] Waiting 30s for NLB deprovisioning..."
  if [ "$DRY_RUN" != "true" ]; then
    sleep 30
  fi
else
  warn "kubectl not available, skipping K8s resource cleanup"
fi

########################################
# Step 2: K8s Components teardown
########################################
echo ""
echo "============================================"
echo "  Step 2: Removing K8s Components"
echo "============================================"

if [ "$KUBECTL_OK" = "true" ]; then
  # OpenClaw Operator
  log "[2.1] Deleting OpenClaw Operator..."
  if [ "$DRY_RUN" != "true" ]; then
    kubectl delete deployment openclaw-operator -n openclaw-operator-system --ignore-not-found --timeout=60s 2>/dev/null || true
    kubectl delete sa openclaw-operator -n openclaw-operator-system --ignore-not-found 2>/dev/null || true
    kubectl delete clusterrolebinding openclaw-operator-manager-rolebinding --ignore-not-found 2>/dev/null || true
    kubectl delete clusterrole openclaw-operator-manager-role --ignore-not-found 2>/dev/null || true
    kubectl delete namespace openclaw-operator-system --ignore-not-found --timeout=120s 2>/dev/null || true
  else
    echo "  (dry-run) delete operator deployment, RBAC, namespace"
  fi

  # CRD
  log "[2.2] Deleting OpenClaw CRD..."
  if [ "$DRY_RUN" != "true" ]; then
    kubectl delete crd openclawinstances.openclaw.rocks --ignore-not-found --timeout=60s 2>/dev/null || true
  else
    echo "  (dry-run) kubectl delete crd openclawinstances.openclaw.rocks"
  fi

  # StorageClasses
  log "[2.3] Deleting custom StorageClasses..."
  if [ "$DRY_RUN" != "true" ]; then
    kubectl delete sc efs-sc gp3 --ignore-not-found 2>/dev/null || true
  else
    echo "  (dry-run) kubectl delete sc efs-sc gp3"
  fi

  # ALB Controller (Helm)
  log "[2.4] Uninstalling ALB Controller..."
  if [ "$DRY_RUN" != "true" ]; then
    helm uninstall aws-load-balancer-controller -n kube-system --wait --timeout 120s 2>/dev/null || true
  else
    echo "  (dry-run) helm uninstall aws-load-balancer-controller"
  fi

  # EFS CSI Driver (Helm)
  log "[2.5] Uninstalling EFS CSI Driver..."
  if [ "$DRY_RUN" != "true" ]; then
    helm uninstall aws-efs-csi-driver -n kube-system --wait --timeout 120s 2>/dev/null || true
  else
    echo "  (dry-run) helm uninstall aws-efs-csi-driver"
  fi
else
  warn "kubectl not available, skipping K8s resource cleanup"
fi

# Pod Identity Associations (AWS API, works regardless of kubectl)
log "[2.6] Deleting Pod Identity Associations..."
ASSOCIATIONS=$(aws eks list-pod-identity-associations \
  --cluster-name "$CLUSTER_NAME" --region "$REGION" \
  --query 'associations[*].associationId' --output text 2>/dev/null || echo "")

if [ -n "$ASSOCIATIONS" ] && [ "$ASSOCIATIONS" != "None" ]; then
  for assoc_id in $ASSOCIATIONS; do
    if [ "$DRY_RUN" != "true" ]; then
      aws eks delete-pod-identity-association \
        --cluster-name "$CLUSTER_NAME" \
        --association-id "$assoc_id" \
        --region "$REGION" 2>/dev/null || true
      echo "  Deleted: $assoc_id"
    else
      echo "  (dry-run) delete association: $assoc_id"
    fi
  done
else
  echo "  No associations found"
fi

########################################
# Step 1: CloudFormation stack deletion
########################################
echo ""
echo "============================================"
echo "  Step 1: Deleting CloudFormation Stack"
echo "============================================"

log "[1.1] Deleting stack: $STACK_NAME ..."
if [ "$DRY_RUN" != "true" ]; then
  aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$REGION" 2>&1
  echo "  Stack deletion initiated."
  echo ""
  log "[1.2] Waiting for stack deletion (this may take 15-30 minutes)..."
  echo "  Resources being deleted: EKS cluster, RDS, SQS, VPC, IAM roles, EFS, etc."
  echo ""
  echo "  Monitor progress:"
  echo "    aws cloudformation describe-stack-events --stack-name $STACK_NAME --region $REGION --query 'StackEvents[0:5].[ResourceType,ResourceStatus,ResourceStatusReason]' --output table"
  echo ""

  # Wait with timeout
  aws cloudformation wait stack-delete-complete \
    --stack-name "$STACK_NAME" --region "$REGION" 2>&1 && {
    echo ""
    echo -e "${GREEN}============================================${NC}"
    echo -e "${GREEN}  ✅ All resources destroyed successfully!${NC}"
    echo -e "${GREEN}============================================${NC}"
  } || {
    echo ""
    err "Stack deletion may have failed or timed out."
    echo "  Check status:"
    echo "    aws cloudformation describe-stacks --stack-name $STACK_NAME --region $REGION --query 'Stacks[0].StackStatus' --output text"
    echo ""
    echo "  If DELETE_FAILED, check which resources failed:"
    echo "    aws cloudformation describe-stack-resources --stack-name $STACK_NAME --region $REGION --query 'StackResources[?ResourceStatus==\`DELETE_FAILED\`].[LogicalResourceId,ResourceStatusReason]' --output table"
    echo ""
    echo "  Then retry with --retain-resources:"
    echo "    aws cloudformation delete-stack --stack-name $STACK_NAME --region $REGION --retain-resources <failed-resource>"
    exit 1
  }
else
  echo "  (dry-run) aws cloudformation delete-stack --stack-name $STACK_NAME --region $REGION"
  echo ""
  echo -e "${YELLOW}============================================${NC}"
  echo -e "${YELLOW}  DRY RUN complete — nothing was deleted${NC}"
  echo -e "${YELLOW}============================================${NC}"
fi
