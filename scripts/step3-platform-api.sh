#!/bin/bash
set -euo pipefail

########################################
# OpenClaw CN Workshop - Step 3
# Platform API Deployment Script
# Run this on the IDE instance after Step 1 + Step 2.
########################################

STACK_NAME="${STACK_NAME:-openclaw-cn-workshop}"
REGION="${REGION:-cn-northwest-1}"
ADMIN_EMAIL="${ADMIN_EMAIL:-openclaw-saas-admin@example.com}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-OpenClaw2026!}"

# All images from public.ecr.aws (accessible from CN)
PLATFORM_IMAGE="${PLATFORM_IMAGE:-public.ecr.aws/h4t8a9b8/openclaw-saas/platform:v0.9.25-china}"
BILLING_IMAGE="${BILLING_IMAGE:-public.ecr.aws/h4t8a9b8/openclaw-saas/billing-consumer:v0.1.0}"
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
NLB_SG_ID=$(get_output PlatformNLBSecurityGroupId)

echo "  Cluster: $CLUSTER_NAME"
echo "  RDS: $RDS_ENDPOINT"
echo "  SQS: $SQS_QUEUE_URL"
echo "  NLB SG: $NLB_SG_ID"

# Setup kubeconfig
echo ">>> Configuring kubectl..."
aws eks update-kubeconfig --name "$CLUSTER_NAME" --region "$REGION"

# 1. Get RDS password
echo ""
echo ">>> [1/10] Retrieving RDS password..."
DB_PASSWORD=$(aws secretsmanager get-secret-value --secret-id "$RDS_SECRET_ARN" --region "$REGION" \
  --query 'SecretString' --output text | python3 -c "import sys,json; print(json.load(sys.stdin)['password'])")
DATABASE_URL="postgresql+asyncpg://openclaw_admin:${DB_PASSWORD}@${RDS_ENDPOINT}:5432/openclawsaas"

# 2. Generate JWT secret
JWT_SECRET=$(python3 -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits) for _ in range(64)))")

# 3. Create namespace
echo ">>> [2/10] Creating namespace..."
kubectl create namespace openclaw-platform --dry-run=client -o yaml | kubectl apply -f -

# 4. Create Pod Identity Association for platform-api SA
echo ">>> [3/10] Creating Pod Identity Association..."
aws eks create-pod-identity-association \
  --cluster-name "$CLUSTER_NAME" \
  --namespace openclaw-platform \
  --service-account platform-api \
  --role-arn "$PLATFORM_API_ROLE_ARN" \
  --region "$REGION" 2>/dev/null || echo "  (already exists)"

# 5. Create RBAC for platform-api ServiceAccount (least-privilege)
echo ">>> [4/10] Creating RBAC..."
# Delete existing bindings first — K8s does not allow changing roleRef in-place
kubectl delete clusterrolebinding platform-api-binding --ignore-not-found 2>/dev/null
kubectl delete clusterrolebinding platform-api-pod-logs --ignore-not-found 2>/dev/null
cat <<'RBAC_EOF' | kubectl apply -f -
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: openclaw-platform-api
rules:
  # OpenClaw CRD management
  - apiGroups: ["openclaw.rocks"]
    resources: ["openclawinstances"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  # Namespace lifecycle (tenant-* namespaces)
  - apiGroups: [""]
    resources: ["namespaces"]
    verbs: ["get", "list", "watch", "create", "update", "delete"]
  # Core resources in tenant namespaces
  - apiGroups: [""]
    resources: ["resourcequotas", "limitranges", "secrets", "pods", "services", "configmaps"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  # Network policies for tenant isolation
  - apiGroups: ["networking.k8s.io"]
    resources: ["networkpolicies"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  # Read-only for statefulsets and PVCs (status monitoring)
  - apiGroups: ["apps"]
    resources: ["statefulsets"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["persistentvolumeclaims"]
    verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: platform-api-pod-logs
rules:
  - apiGroups: [""]
    resources: ["pods", "pods/log"]
    verbs: ["get", "list"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: platform-api-binding
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: openclaw-platform-api
subjects:
  - kind: ServiceAccount
    name: platform-api
    namespace: openclaw-platform
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: platform-api-pod-logs
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: platform-api-pod-logs
subjects:
  - kind: ServiceAccount
    name: platform-api
    namespace: openclaw-platform
RBAC_EOF

# 6. Create secrets
echo ">>> [5/10] Creating K8s secrets..."
kubectl create secret generic platform-db-secret \
  --namespace openclaw-platform \
  --from-literal="DATABASE_URL=$DATABASE_URL" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic platform-config \
  --namespace openclaw-platform \
  --from-literal="AWS_ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)" \
  --from-literal="AWS_PARTITION=aws-cn" \
  --from-literal="AWS_REGION=$REGION" \
  --from-literal="ECR_REGISTRY=public.ecr.aws/h4t8a9b8/openclaw-saas" \
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
echo ">>> [6/10] Deploying Platform API..."
cat "$SCRIPT_DIR/../yaml/platform-api.yaml" | \
  sed "s|\${PLATFORM_IMAGE}|$PLATFORM_IMAGE|g" | \
  sed "s|\${PLATFORM_REPLICAS}|$PLATFORM_REPLICAS|g" | \
  sed "s|\${NLB_SG_ID}|$NLB_SG_ID|g" | \
  kubectl apply -f -

echo ">>> [7/10] Waiting for rollout..."
kubectl rollout status deployment/platform-api -n openclaw-platform --timeout=300s

# 8. Run database migrations
echo ">>> [8/10] Running database migrations..."
kubectl exec deploy/platform-api -n openclaw-platform -- python3 -m api.migrations.add_usage_tables
kubectl exec deploy/platform-api -n openclaw-platform -- python3 -m api.migrations.add_custom_image_columns

# 9. Deploy billing-consumer (SQS consumer + usage aggregator)
echo ">>> [9/10] Deploying Billing Consumer..."
cat "$SCRIPT_DIR/../yaml/billing-consumer.yaml" | \
  sed "s|\${BILLING_IMAGE}|$BILLING_IMAGE|g" | \
  kubectl apply -f -

echo ">>> [10/10] Waiting for billing-consumer rollout..."
kubectl rollout status deployment/billing-consumer -n openclaw-platform --timeout=120s

echo ""
echo "============================================"
echo "  Step 3 Complete!"
echo "============================================"
echo ""
echo ">>> Platform API pods:"
kubectl get pods -n openclaw-platform
echo ""
echo ">>> Billing Consumer pods:"
kubectl get pods -n openclaw-platform -l app.kubernetes.io/name=billing-consumer
echo ""
echo ">>> Service (NLB may take 2-3 minutes):"
kubectl get svc -n openclaw-platform
echo ""
NLB_HOST=$(kubectl get svc platform-api -n openclaw-platform -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "pending")
echo ">>> Platform API endpoint: http://${NLB_HOST}:8890"
echo ">>> Admin: $ADMIN_EMAIL"
