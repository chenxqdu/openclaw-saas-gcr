#!/usr/bin/env bash
# apply.sh — Deploy openclaw-platform onto an existing EKS cluster.
# Creates all required AWS resources, then installs/upgrades the Helm chart.
#
# Usage:
#   bash apply.sh
#   bash apply.sh --uninstall
#
# Optional env overrides:
#   CLUSTER_NAME       EKS cluster name (default: openclaw-workshop)
#   ADMIN_EMAIL        Platform admin email
#   ADMIN_PASSWORD     Platform admin password
#   HELM_RELEASE       Helm release name (default: openclaw-platform)
#   HELM_NAMESPACE     K8s namespace (default: openclaw-platform)

set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:-openclaw-workshop}"
ADMIN_EMAIL="${ADMIN_EMAIL:-admin@workshop.openclaw.dev}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
HELM_RELEASE="${HELM_RELEASE:-openclaw-platform}"
HELM_NAMESPACE="${HELM_NAMESPACE:-openclaw-platform}"
IAM_ROLE_NAME="${HELM_RELEASE}-api"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log()  { echo -e "\033[1;32m▸\033[0m $*"; }
warn() { echo -e "\033[1;33m⚠\033[0m $*"; }
err()  { echo -e "\033[1;31m✖ $*\033[0m" >&2; exit 1; }

# ── Uninstall ──────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--uninstall" ]]; then
  log "Uninstalling $HELM_RELEASE..."
  helm uninstall "$HELM_RELEASE" -n "$HELM_NAMESPACE" 2>/dev/null || true
  kubectl delete ns "$HELM_NAMESPACE" --ignore-not-found
  kubectl delete clusterrole "$HELM_RELEASE" --ignore-not-found
  kubectl delete clusterrolebinding "$HELM_RELEASE" --ignore-not-found

  log "Removing Pod Identity Association..."
  ASSOC_ID=$(aws eks list-pod-identity-associations --cluster-name "$CLUSTER_NAME" \
    --namespace "$HELM_NAMESPACE" --service-account "$HELM_RELEASE" \
    --region "$AWS_REGION" --query 'associations[0].associationId' --output text 2>/dev/null || echo "")
  [[ -n "$ASSOC_ID" && "$ASSOC_ID" != "None" ]] && \
    aws eks delete-pod-identity-association --cluster-name "$CLUSTER_NAME" \
      --association-id "$ASSOC_ID" --region "$AWS_REGION" && log "Pod Identity removed"

  log "Removing IAM role $IAM_ROLE_NAME..."
  aws iam delete-role-policy --role-name "$IAM_ROLE_NAME" --policy-name sqs-access 2>/dev/null || true
  aws iam delete-role --role-name "$IAM_ROLE_NAME" 2>/dev/null || true

  SQS_URL=$(aws sqs get-queue-url --queue-name "${CLUSTER_NAME}-ps-usage-events" \
    --region "$AWS_REGION" --query QueueUrl --output text 2>/dev/null || echo "")
  [[ -n "$SQS_URL" ]] && aws sqs delete-queue --queue-url "$SQS_URL" --region "$AWS_REGION" && log "SQS queue deleted"

  log "Done."
  exit 0
fi

# ── Pre-flight ─────────────────────────────────────────────────────────────
for cmd in kubectl aws helm; do
  command -v "$cmd" >/dev/null || err "$cmd not found"
done
kubectl cluster-info >/dev/null 2>&1 || err "kubectl cannot reach cluster"

AWS_REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null || echo us-west-2)}"
AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"
AWS_PARTITION="aws"; [[ "$AWS_REGION" == cn-* ]] && AWS_PARTITION="aws-cn"

log "Cluster: $(kubectl config current-context)"
log "Account: $AWS_ACCOUNT_ID  Region: $AWS_REGION"

# ── 1. SQS queue ───────────────────────────────────────────────────────────
QUEUE_NAME="${CLUSTER_NAME}-ps-usage-events"
log "SQS queue: $QUEUE_NAME"
SQS_QUEUE_URL=""
for attempt in 1 2 3 4; do
  SQS_QUEUE_URL=$(aws sqs create-queue --queue-name "$QUEUE_NAME" \
    --region "$AWS_REGION" --query QueueUrl --output text 2>/dev/null) && break
  ERR=$(aws sqs create-queue --queue-name "$QUEUE_NAME" \
    --region "$AWS_REGION" --query QueueUrl --output text 2>&1 || true)
  if echo "$ERR" | grep -q QueueDeletedRecently; then
    warn "SQS 60s cooldown after deletion, waiting... ($attempt/3)"
    sleep 20
  else
    # Queue already exists — just get the URL
    SQS_QUEUE_URL=$(aws sqs get-queue-url --queue-name "$QUEUE_NAME" \
      --region "$AWS_REGION" --query QueueUrl --output text) && break
  fi
done
[[ -n "$SQS_QUEUE_URL" ]] || err "Failed to create/find SQS queue after retries"
log "SQS URL: $SQS_QUEUE_URL"

# ── 2. IAM Role + Pod Identity ─────────────────────────────────────────────
QUEUE_ARN="arn:${AWS_PARTITION}:sqs:${AWS_REGION}:${AWS_ACCOUNT_ID}:${QUEUE_NAME}"
ROLE_ARN="arn:${AWS_PARTITION}:iam::${AWS_ACCOUNT_ID}:role/${IAM_ROLE_NAME}"

log "IAM role: $IAM_ROLE_NAME"
if ! aws iam get-role --role-name "$IAM_ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role \
    --role-name "$IAM_ROLE_NAME" \
    --assume-role-policy-document '{
      "Version":"2012-10-17",
      "Statement":[{"Effect":"Allow","Principal":{"Service":"pods.eks.amazonaws.com"},
        "Action":["sts:AssumeRole","sts:TagSession"]}]}' \
    --query 'Role.Arn' --output text >/dev/null
  log "IAM role created"
fi

aws iam put-role-policy \
  --role-name "$IAM_ROLE_NAME" \
  --policy-name sqs-access \
  --policy-document "{
    \"Version\":\"2012-10-17\",
    \"Statement\":[{\"Effect\":\"Allow\",
      \"Action\":[\"sqs:SendMessage\",\"sqs:ReceiveMessage\",\"sqs:DeleteMessage\",\"sqs:GetQueueAttributes\"],
      \"Resource\":\"${QUEUE_ARN}\"}]}" >/dev/null
log "SQS policy attached"

log "Pod Identity Association..."
ASSOC_ID=$(aws eks list-pod-identity-associations --cluster-name "$CLUSTER_NAME" \
  --namespace "$HELM_NAMESPACE" --service-account "$HELM_RELEASE" \
  --region "$AWS_REGION" --query 'associations[0].associationId' --output text 2>/dev/null || echo "")
if [[ -z "$ASSOC_ID" || "$ASSOC_ID" == "None" ]]; then
  aws eks create-pod-identity-association \
    --cluster-name "$CLUSTER_NAME" \
    --namespace "$HELM_NAMESPACE" \
    --service-account "$HELM_RELEASE" \
    --role-arn "$ROLE_ARN" \
    --region "$AWS_REGION" >/dev/null
  log "Pod Identity Association created"
else
  log "Pod Identity Association already exists ($ASSOC_ID)"
fi

# ── 3. Helm install/upgrade ────────────────────────────────────────────────
# Prompt for password if not set
if [[ -z "${ADMIN_PASSWORD}" ]]; then
  read -rsp "Admin password (ADMIN_PASSWORD): " ADMIN_PASSWORD
  echo
fi
[[ -n "$ADMIN_PASSWORD" ]] || err "ADMIN_PASSWORD is required"

log "Helm install/upgrade $HELM_RELEASE → $HELM_NAMESPACE..."
# create-namespace first so Pod Identity Association can reference it
kubectl create namespace "$HELM_NAMESPACE" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
helm upgrade --install "$HELM_RELEASE" "$SCRIPT_DIR" \
  --namespace "$HELM_NAMESPACE" --create-namespace \
  --set config.sqsQueueUrl="$SQS_QUEUE_URL" \
  --set config.awsRegion="$AWS_REGION" \
  --set config.awsAccountId="$AWS_ACCOUNT_ID" \
  --set config.adminEmail="$ADMIN_EMAIL" \
  --set config.adminPassword="$ADMIN_PASSWORD" \
  --set config.awsPartition="$AWS_PARTITION"

# Pod Identity webhook needs a few seconds to sync the new association.
# Delete pods (not rollout restart) to force re-admission through the webhook.
log "Restarting pods to pick up Pod Identity token..."
sleep 5
kubectl delete pods -n "$HELM_NAMESPACE" -l "app.kubernetes.io/instance=$HELM_RELEASE" --wait=false >/dev/null 2>&1 || true

# ── 4. Wait for NLB ────────────────────────────────────────────────────────
log "Waiting for NLB endpoint..."
NLB_HOST=""
for i in $(seq 1 36); do
  NLB_HOST=$(kubectl get svc "$HELM_RELEASE" -n "$HELM_NAMESPACE" \
    -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)
  [[ -n "$NLB_HOST" ]] && break
  echo "    Waiting... ($i/36)"
  sleep 5
done

echo ""
if [[ -n "$NLB_HOST" ]]; then
  echo "========================================"
  echo "  Provisioning Service Ready"
  echo "========================================"
  echo ""
  echo "  URL:         http://$NLB_HOST"
  echo "  Health:      http://$NLB_HOST/health"
  echo "  API Docs:    http://$NLB_HOST/docs"
  echo "  Web Console: http://$NLB_HOST/console"
  echo ""
  echo "  Admin:       $ADMIN_EMAIL"
  echo "  Password:    $ADMIN_PASSWORD"
  echo "========================================"
  echo ""
  echo "export PROVISIONING_SERVICE_URL=http://$NLB_HOST"
else
  warn "NLB not ready yet. Check: kubectl get svc "$HELM_RELEASE" -n $HELM_NAMESPACE"
fi
