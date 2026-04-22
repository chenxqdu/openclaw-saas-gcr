#!/bin/bash
set -euo pipefail

########################################
# OpenClaw CN Workshop - Step 3
# Platform API Deployment Script
# Run this on the IDE instance after Step 1 + Step 2.
########################################

STACK_NAME="${STACK_NAME:-openclaw-cn-workshop}"
REGION="${REGION:-cn-northwest-1}"
ADMIN_EMAIL="${ADMIN_EMAIL:-chenxqdu@amazon.com}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-OpenClaw2026!}"
PLATFORM_IMAGE="${PLATFORM_IMAGE:-public.ecr.aws/i4x4j7g8/openclaw-saas/platform:v0.9.17}"
PLATFORM_REPLICAS="${PLATFORM_REPLICAS:-2}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "============================================"
echo "  OpenClaw Workshop - Step 3: Platform API"
echo "  Stack: $STACK_NAME | Region: $REGION"
echo "============================================"

# Get outputs from Step 1
echo ">>> Fetching Step 1 outputs..."
get_output() {
  aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}

CLUSTER_NAME=$(get_output ClusterName)
RDS_ENDPOINT=$(get_output RDSEndpoint)
RDS_SECRET_ARN=$(get_output RDSSecretArn)
SQS_QUEUE_URL=$(get_output SQSQueueUrl)
PLATFORM_API_ROLE_ARN=$(get_output PlatformApiRoleArn)

echo "  Cluster: $CLUSTER_NAME"
echo "  RDS: $RDS_ENDPOINT"
echo "  SQS: $SQS_QUEUE_URL"

# Setup kubeconfig
echo ">>> Configuring kubectl..."
aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$REGION"

# 1. Get RDS password
echo ""
echo ">>> [1/8] Retrieving RDS password..."
DB_PASSWORD=$(aws secretsmanager get-secret-value --secret-id "$RDS_SECRET_ARN" --region "$REGION" \
  --query 'SecretString' --output text | python3 -c "import sys,json; print(json.load(sys.stdin)['password'])")
DATABASE_URL="postgresql+asyncpg://openclaw_admin:${DB_PASSWORD}@${RDS_ENDPOINT}:5432/openclawsaas"

# 2. Generate JWT secret
JWT_SECRET=$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(64)))")

# 3. Create namespace
echo ">>> [2/8] Creating namespace..."
kubectl create namespace openclaw-platform --dry-run=client -o yaml | kubectl apply -f -

# 4. Create Pod Identity Association for platform-api SA
echo ">>> [3/8] Creating Pod Identity Association..."
aws eks create-pod-identity-association \
  --cluster-name "$CLUSTER_NAME" \
  --namespace openclaw-platform \
  --service-account platform-api \
  --role-arn "$PLATFORM_API_ROLE_ARN" \
  --region "$REGION" 2>/dev/null || echo "  (already exists)"

# 5. Create RBAC for platform-api ServiceAccount
echo ">>> [4/8] Creating RBAC..."
cat <<'RBAC_EOF' | kubectl apply -f -
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: platform-api-binding
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-admin
subjects:
  - kind: ServiceAccount
    name: platform-api
    namespace: openclaw-platform
RBAC_EOF

# 6. Create secrets
echo ">>> [5/8] Creating K8s secrets..."
kubectl create secret generic platform-db-secret \
  --namespace openclaw-platform \
  --from-literal="DATABASE_URL=$DATABASE_URL" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic platform-config \
  --namespace openclaw-platform \
  --from-literal="AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)" \
  --from-literal="AWS_PARTITION=aws-cn" \
  --from-literal="AWS_REGION=$REGION" \
  --from-literal="ECR_REGISTRY=public.ecr.aws/i4x4j7g8/openclaw-saas" \
  --from-literal="JWT_SECRET=$JWT_SECRET" \
  --from-literal="METRICS_EXPORTER_TAG=v0.1.0" \
  --from-literal="SQS_QUEUE_URL=$SQS_QUEUE_URL" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic platform-admin-seed \
  --namespace openclaw-platform \
  --from-literal="ADMIN_EMAIL=$ADMIN_EMAIL" \
  --from-literal="ADMIN_PASSWORD=$ADMIN_PASSWORD" \
  --dry-run=client -o yaml | kubectl apply -f -

# 7. Deploy platform-api
echo ">>> [6/8] Deploying Platform API..."
cat "$SCRIPT_DIR/../yaml/platform-api.yaml" | \
  sed "s|\${PLATFORM_IMAGE}|$PLATFORM_IMAGE|g" | \
  sed "s|\${PLATFORM_REPLICAS}|$PLATFORM_REPLICAS|g" | \
  kubectl apply -f -

echo ">>> [7/8] Waiting for rollout..."
kubectl rollout status deployment/platform-api -n openclaw-platform --timeout=300s

# 8. Run database migration for usage tables
echo ">>> [8/8] Running database migration..."
kubectl exec deploy/platform-api -n openclaw-platform -- python3 -m api.migrations.add_usage_tables

echo ""
echo "============================================"
echo "  Step 3 Complete!"
echo "============================================"
echo ""
echo ">>> Platform API pods:"
kubectl get pods -n openclaw-platform
echo ""
echo ">>> Service (NLB may take 2-3 minutes):"
kubectl get svc -n openclaw-platform
echo ""
NLB_HOST=$(kubectl get svc platform-api -n openclaw-platform -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "pending")
echo ">>> Platform API endpoint: http://${NLB_HOST}:8890"
echo ">>> Admin: $ADMIN_EMAIL"
