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
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
YAML_DIR="${SCRIPT_DIR}/../yaml"

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
echo -e "${YELLOW}NOTE: Stack deletion requires full AWS permissions (IAM, EC2, RDS, EFS, etc.)."
echo -e "The IDE role does NOT have these permissions."
echo -e "Run this script from a terminal with AdministratorAccess or equivalent credentials.${NC}"
echo ""

if [ "$DRY_RUN" = "true" ]; then
  warn "DRY RUN mode — no resources will be deleted"
  echo ""
fi

# Confirm
if [ "$DRY_RUN" != "true" ]; then
  echo -e "${RED}⚠️  This will PERMANENTLY DELETE all workshop resources:${NC}"
  echo "  - Hermes Sandbox + hermes namespace (if deployed via step4)"
  echo "  - Karpenter + sandbox NodePool + agent-sandbox controller/CRDs (if deployed)"
  echo "  - Tenant namespaces (tenant-*) + OpenClawInstance CRs + their PVCs"
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
# Step 4: Hermes Sandbox + Karpenter teardown
# (runs first so Karpenter-provisioned nodes drain
#  before downstream namespaces/CRDs disappear)
########################################
echo ""
echo "============================================"
echo "  Step 4: Removing Hermes Sandbox + Karpenter"
echo "============================================"

if [ "$KUBECTL_OK" = "true" ]; then
  # Each step is idempotent / --ignore-not-found, so it's safe to run
  # unconditionally — covers both variants (Karpenter and static
  # sandbox-nodes). The static sandbox managed nodegroup itself is
  # torn down as part of CFN stack deletion (Step 1 reference below);
  # destroy.sh does NOT delete it.
  has_hermes_resources=false
  kubectl get ns hermes &>/dev/null && has_hermes_resources=true
  kubectl get crd sandboxes.agents.x-k8s.io &>/dev/null && has_hermes_resources=true
  { kubectl get nodepool sandbox &>/dev/null || helm -n kube-system status karpenter &>/dev/null; } && has_hermes_resources=true

  if [ "$has_hermes_resources" = "true" ]; then
    log "[4.1] Deleting Hermes Sandbox + namespace..."
    if [ "$DRY_RUN" != "true" ]; then
      kubectl delete sandbox hermes-feishu-sandbox -n hermes --ignore-not-found --timeout=60s 2>/dev/null || true
      kubectl delete namespace hermes --ignore-not-found --timeout=120s 2>/dev/null || true
    else
      echo "  (dry-run) kubectl delete sandbox + namespace hermes"
    fi

    log "[4.2] Deleting Karpenter NodePool + EC2NodeClass (Karpenter variant only)..."
    if [ "$DRY_RUN" != "true" ]; then
      kubectl delete nodepool sandbox --ignore-not-found --timeout=120s 2>/dev/null || true
      kubectl delete ec2nodeclass sandbox --ignore-not-found --timeout=60s 2>/dev/null || true
      # Give Karpenter time to terminate any provisioned nodes before
      # we uninstall the controller itself.
      if kubectl -n kube-system get deploy karpenter &>/dev/null; then
        sleep 30
      fi
    else
      echo "  (dry-run) kubectl delete nodepool/ec2nodeclass sandbox (no-op for static variant)"
    fi

    log "[4.3] Uninstalling agent-sandbox controller + CRDs..."
    if [ "$DRY_RUN" != "true" ]; then
      # Use vendored yaml (yaml/agent-sandbox-*.yaml). The ECR_REGISTRY
      # placeholder in those files expands to any non-empty value here —
      # kubectl delete matches resources by name, not image, so the
      # registry value is irrelevant for the delete operation.
      for f in "${YAML_DIR}/agent-sandbox-extensions.yaml" "${YAML_DIR}/agent-sandbox-manifest.yaml"; do
        if [ -f "$f" ]; then
          sed "s|\${ECR_REGISTRY}|placeholder|g" "$f" \
            | kubectl delete --ignore-not-found -f - 2>/dev/null || true
        fi
      done
    else
      echo "  (dry-run) kubectl delete -f yaml/agent-sandbox-{manifest,extensions}.yaml"
    fi

    log "[4.4] Uninstalling Karpenter (Karpenter variant only)..."
    if [ "$DRY_RUN" != "true" ]; then
      helm uninstall karpenter -n kube-system --wait --timeout 120s 2>/dev/null || true
    else
      echo "  (dry-run) helm uninstall karpenter (no-op for static variant)"
    fi
  else
    echo "  Hermes / agent-sandbox / Karpenter not deployed — skipping Step 4 cleanup"
  fi
else
  warn "kubectl not available, skipping Step 4 resource cleanup"
fi

########################################
# Step 3: Platform API teardown
########################################
echo ""
echo "============================================"
echo "  Step 3: Removing Platform API"
echo "============================================"

if [ "$KUBECTL_OK" = "true" ]; then
  # Delete NLB service first — the load-balancer-controller / cloud
  # provider needs to deregister targets, remove listeners, and
  # deprovision the NLB itself before the k8s Service delete returns.
  # In cn-northwest-1 this commonly takes 90-120s; 60s caused orphan
  # NLBs that later blocked VPC deletion.
  log "[3.1] Deleting Platform API service (NLB)..."
  if [ "$DRY_RUN" != "true" ]; then
    kubectl delete svc platform-api -n openclaw-platform --ignore-not-found --timeout=180s 2>/dev/null || true
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
  # Tenant CRs + namespaces — MUST drain before removing the operator,
  # otherwise OpenClawInstance CRs stall forever on finalizers with no
  # operator to reconcile them (which then blocks CRD deletion).
  log "[2.0] Draining tenant namespaces (OpenClawInstance CRs + PVCs + ns)..."
  if [ "$DRY_RUN" != "true" ]; then
    # Delete all OpenClawInstance CRs cluster-wide
    TENANT_NS=$(kubectl get ns -o name 2>/dev/null | grep '^namespace/tenant-' | cut -d/ -f2 || true)
    for ns in $TENANT_NS; do
      kubectl -n "$ns" delete openclawinstance --all --timeout=120s 2>/dev/null || true
    done
    # Give the operator a moment to run finalizers
    sleep 5
    # Delete the tenant namespaces (this reclaims PVCs/EBS volumes via
    # the gp3 StorageClass's Delete reclaim policy)
    for ns in $TENANT_NS; do
      kubectl delete namespace "$ns" --ignore-not-found --timeout=180s 2>/dev/null || true
    done
  else
    TENANT_NS=$(kubectl get ns -o name 2>/dev/null | grep '^namespace/tenant-' | cut -d/ -f2 || true)
    if [ -n "$TENANT_NS" ]; then
      echo "  (dry-run) would delete OpenClawInstance CRs + namespaces in:"
      echo "$TENANT_NS" | sed 's/^/    /'
    else
      echo "  (dry-run) no tenant-* namespaces to drain"
    fi
  fi

  # OpenClaw Operator
  log "[2.1] Deleting OpenClaw Operator..."
  if [ "$DRY_RUN" != "true" ]; then
    kubectl delete daemonset cn-image-prepull -n kube-system --ignore-not-found 2>/dev/null || true
    kubectl delete job -n kube-system -l app=cn-image-prepull --ignore-not-found 2>/dev/null || true
    kubectl delete deployment openclaw-operator -n openclaw-operator-system --ignore-not-found --timeout=60s 2>/dev/null || true
    kubectl delete sa openclaw-operator -n openclaw-operator-system --ignore-not-found 2>/dev/null || true
    kubectl delete clusterrolebinding openclaw-operator-manager-rolebinding --ignore-not-found 2>/dev/null || true
    kubectl delete clusterrole openclaw-operator-manager-role --ignore-not-found 2>/dev/null || true
    kubectl delete namespace openclaw-operator-system --ignore-not-found --timeout=120s 2>/dev/null || true
  else
    echo "  (dry-run) delete operator deployment, RBAC, namespace"
  fi

  # CRDs — operator is already gone, so any remaining CR finalizers
  # must be cleared manually before CRD deletion can succeed.
  log "[2.2] Deleting OpenClaw CRDs..."
  if [ "$DRY_RUN" != "true" ]; then
    # Force-clear finalizers on any leftover CRs (defense in depth —
    # [2.0] should have removed all CRs already). The pipeline has to
    # tolerate all three failure modes because `set -o pipefail` is
    # on: (a) CRD absent → kubectl get exits 1; (b) no CR instances →
    # .items is empty; (c) jq missing on older IDEs.
    for crd in openclawinstances.openclaw.rocks openclawselfconfigs.openclaw.rocks; do
      { kubectl get "$crd" --all-namespaces -o json 2>/dev/null \
          | jq -r '.items[] | "\(.metadata.namespace) \(.metadata.name)"' 2>/dev/null \
          | while read -r ns name; do
              [ -z "$name" ] && continue
              kubectl -n "$ns" patch "$crd" "$name" --type=merge \
                -p '{"metadata":{"finalizers":[]}}' 2>/dev/null || true
            done
      } || true
    done
    kubectl delete crd openclawinstances.openclaw.rocks openclawselfconfigs.openclaw.rocks --ignore-not-found --timeout=60s 2>/dev/null || true
  else
    echo "  (dry-run) kubectl delete crd openclawinstances.openclaw.rocks openclawselfconfigs.openclaw.rocks"
  fi

  # StorageClasses
  log "[2.3] Deleting custom StorageClasses..."
  if [ "$DRY_RUN" != "true" ]; then
    kubectl delete sc efs-sc gp3 --ignore-not-found 2>/dev/null || true
  else
    echo "  (dry-run) kubectl delete sc efs-sc gp3"
  fi

  # ALB Controller — installed by step2 via `kubectl apply -f yaml/
  # aws-load-balancer-controller.yaml` (not Helm). Delete the same
  # yaml to tear down the Deployment, RBAC, Service, webhooks, 7 CRDs,
  # and the IngressClass/IngressClassParams List. Placeholder values
  # are irrelevant for delete — kubectl matches by name/kind, not
  # image ref.
  log "[2.4] Deleting ALB Controller (yaml/aws-load-balancer-controller.yaml)..."
  if [ "$DRY_RUN" != "true" ]; then
    if [ -f "${YAML_DIR}/aws-load-balancer-controller.yaml" ]; then
      sed -e 's|${CLUSTER_NAME}|placeholder|g' \
          -e 's|${ALB_CONTROLLER_ROLE_ARN}|placeholder|g' \
          -e 's|${REGION}|placeholder|g' \
          -e 's|${VPC_ID}|placeholder|g' \
          "${YAML_DIR}/aws-load-balancer-controller.yaml" \
        | kubectl delete --ignore-not-found -f - 2>/dev/null || true
    else
      # Fallback for older deployments that used helm
      helm uninstall aws-load-balancer-controller -n kube-system --wait --timeout 120s 2>/dev/null || true
    fi
  else
    echo "  (dry-run) kubectl delete -f yaml/aws-load-balancer-controller.yaml"
  fi

  # EFS CSI Driver — installed by step2 as an EKS-managed addon
  # (`aws eks create-addon`), not Helm. Delete via the same API.
  log "[2.5] Deleting EFS CSI Driver (EKS addon)..."
  if [ "$DRY_RUN" != "true" ]; then
    aws eks delete-addon \
      --cluster-name "$CLUSTER_NAME" \
      --addon-name aws-efs-csi-driver \
      --region "$REGION" 2>/dev/null || true
    # Also try Helm uninstall as a fallback for legacy deploys.
    helm uninstall aws-efs-csi-driver -n kube-system --wait --timeout 60s 2>/dev/null || true
  else
    echo "  (dry-run) aws eks delete-addon --addon-name aws-efs-csi-driver"
  fi
else
  warn "kubectl not available, skipping K8s resource cleanup"
fi

# Pod Identity Associations — delete ONLY the ones step3 explicitly
# created. Others stay:
#   - efs-csi-controller-sa (created as part of step2's
#     `aws eks create-addon aws-efs-csi-driver`) is owned by that
#     addon and gets cleaned up automatically by
#     `aws eks delete-addon` ([2.5] above).
#   - ebs-csi-controller-sa (created by the CFN EBS CSI addon in
#     Step 1) is owned by CloudFormation; leave it to CFN stack
#     delete so the IaC ownership stays intact.
log "[2.6] Deleting script-created Pod Identity Associations..."
# Each entry: "<namespace>:<serviceAccount>" — matches associations
# whose ns+SA pair is in this list.
OWNED_ASSOCS=(
  "openclaw-platform:platform-api"
)

for entry in "${OWNED_ASSOCS[@]}"; do
  ns="${entry%:*}"
  sa="${entry#*:}"
  assoc_id=$(aws eks list-pod-identity-associations \
    --cluster-name "$CLUSTER_NAME" --region "$REGION" \
    --namespace "$ns" --service-account "$sa" \
    --query 'associations[0].associationId' --output text 2>/dev/null || echo "None")
  if [ -z "$assoc_id" ] || [ "$assoc_id" = "None" ]; then
    echo "  $ns/$sa: no association (skip)"
    continue
  fi
  if [ "$DRY_RUN" != "true" ]; then
    aws eks delete-pod-identity-association \
      --cluster-name "$CLUSTER_NAME" \
      --association-id "$assoc_id" \
      --region "$REGION" 2>/dev/null || true
    echo "  $ns/$sa: deleted ($assoc_id)"
  else
    echo "  (dry-run) $ns/$sa: would delete ($assoc_id)"
  fi
done

########################################
# Step 1: CloudFormation stack deletion
# NOTE: Requires full AWS permissions
# (IAM, EC2, RDS, EFS, etc.)
# The IDE role does NOT have these.
########################################
echo ""
echo "============================================"
echo "  Step 1: CloudFormation Stack"
echo "============================================"

echo ""
echo -e "${YELLOW}⚠️  Stack deletion requires permissions beyond what the IDE role has.${NC}"
echo -e "${YELLOW}   Run the following commands from a terminal with AdministratorAccess:${NC}"
echo ""
echo "  # Delete the stack"
echo "  aws cloudformation delete-stack --stack-name $STACK_NAME --region $REGION"
echo ""
echo "  # Monitor progress"
echo "  aws cloudformation wait stack-delete-complete --stack-name $STACK_NAME --region $REGION"
echo ""
echo "  # If DELETE_FAILED, check which resources failed:"
echo "  aws cloudformation describe-stack-resources --stack-name $STACK_NAME --region $REGION \\"
echo "    --query 'StackResources[?ResourceStatus==\`DELETE_FAILED\`].[LogicalResourceId,ResourceStatusReason]' --output table"
echo ""
echo "  # Retry with --retain-resources if needed:"
echo "  aws cloudformation delete-stack --stack-name $STACK_NAME --region $REGION --retain-resources <failed-resource>"
echo ""

if [ "$DRY_RUN" != "true" ]; then
  echo -e "${GREEN}============================================${NC}"
  echo -e "${GREEN}  ✅ Step 2 & 3 cleanup complete!${NC}"
  echo -e "${GREEN}  ⏳ Delete the CFN stack manually (see above)${NC}"
  echo -e "${GREEN}============================================${NC}"
else
  echo -e "${YELLOW}============================================${NC}"
  echo -e "${YELLOW}  DRY RUN complete — nothing was deleted${NC}"
  echo -e "${YELLOW}============================================${NC}"
fi
