#!/usr/bin/env bash
set -euo pipefail

# OpenClaw SaaS Infrastructure Deployment Script
#
# This script deploys the complete OpenClaw SaaS platform:
# 1. CDK stacks (VPC, EKS, EFS, RDS, SQS, S3, IAM)
# 2. ECR repositories (via AWS CLI)
# 3. Kubernetes components (ALB controller, openclaw-operator)
# 4. Platform API application
#
# Prerequisites:
#   - AWS CLI configured
#   - CDK CLI installed (npm install -g aws-cdk)
#   - kubectl installed
#   - helm installed
#   - jq installed
#
# Usage:
#   ./scripts/deploy.sh [OPTIONS]
#
# Options:
#   --skip-cdk          Skip CDK deployment
#   --skip-k8s          Skip Kubernetes deployment
#   --platform-version  Platform image version (default: latest from ECR)
#   -h, --help          Show this help message

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Default options
SKIP_CDK=false
SKIP_K8S=false
PLATFORM_VERSION=""

# Load .env file if present
ENV_FILE="${REPO_ROOT}/.env"
if [[ -f "${ENV_FILE}" ]]; then
  echo -e "\033[0;32m==>\033[0m Loading configuration from ${ENV_FILE}"
  set -a; source "${ENV_FILE}"; set +a
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Parse command line arguments
while [[ $# -gt 0 ]]; do
  case $1 in
    --skip-cdk)
      SKIP_CDK=true
      shift
      ;;
    --skip-k8s)
      SKIP_K8S=true
      shift
      ;;
    --platform-version)
      PLATFORM_VERSION="$2"
      shift 2
      ;;
    -h|--help)
      grep '^#' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *)
      echo -e "${RED}Unknown option: $1${NC}"
      exit 1
      ;;
  esac
done

log_info() {
  echo -e "${GREEN}==>${NC} $*"
}

log_warn() {
  echo -e "${YELLOW}WARNING:${NC} $*"
}

log_error() {
  echo -e "${RED}ERROR:${NC} $*"
  exit 1
}

check_prerequisites() {
  log_info "Checking prerequisites..."

  local missing=()

  command -v aws &> /dev/null || missing+=("aws-cli")
  command -v cdk &> /dev/null || missing+=("aws-cdk")
  command -v kubectl &> /dev/null || missing+=("kubectl")
  command -v helm &> /dev/null || missing+=("helm")
  command -v jq &> /dev/null || missing+=("jq")

  if [[ ${#missing[@]} -gt 0 ]]; then
    log_error "Missing required tools: ${missing[*]}"
  fi

  # Check AWS credentials
  if ! aws sts get-caller-identity &> /dev/null; then
    log_error "AWS credentials not configured. Run 'aws configure'"
  fi

  log_info "All prerequisites met"
}

get_stack_output() {
  local stack_name="$1"
  local output_key="$2"

  # If CF_STACK_NAME is set (CloudFormation mode), all outputs come from one stack
  if [[ -n "${CF_STACK_NAME:-}" ]]; then
    stack_name="${CF_STACK_NAME}"
  fi

  aws cloudformation describe-stacks \
    --stack-name "${stack_name}" \
    --query "Stacks[0].Outputs[?OutputKey=='${output_key}'].OutputValue" \
    --output text 2>/dev/null || echo ""
}

deploy_cdk() {
  log_info "Deploying CDK stacks..."

  cd "${REPO_ROOT}/cdk"

  # Activate virtual environment (create if missing)
  if [[ ! -d ".venv" ]]; then
    log_info "Creating Python virtual environment..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -q -r requirements.txt
  else
    source .venv/bin/activate
  fi

  # Get project name and environment from cdk.json
  PROJECT_NAME=$(jq -r '.context.project_name' cdk.json)
  ENVIRONMENT=$(jq -r '.context.environment' cdk.json)
  STACK_PREFIX="${PROJECT_NAME}-${ENVIRONMENT}"
  if [[ -z "${ENVIRONMENT}" || "${ENVIRONMENT}" == "null" ]]; then
    STACK_PREFIX="${PROJECT_NAME}"
  fi

  log_info "Project: ${PROJECT_NAME}, Environment: ${ENVIRONMENT:-<none>}, Stack prefix: ${STACK_PREFIX}"

  # Bootstrap CDK (if not already done)
  log_info "Bootstrapping CDK..."
  cdk bootstrap

  # Build CDK context args — pass deployer role for EKS Access Entry
  local cdk_context=""
  local caller_arn=$(aws sts get-caller-identity --query 'Arn' --output text 2>/dev/null || echo "")
  if [[ -n "${caller_arn}" ]]; then
    # Convert assumed-role ARN to role ARN for Access Entry
    local deployer_role=$(echo "${caller_arn}" | sed 's|:assumed-role/|:role/|; s|/i-[^/]*$||; s|/[^/]*$||')
    # Only pass if it looks like a role ARN (not user ARN)
    if [[ "${deployer_role}" == *":role/"* ]]; then
      cdk_context+="-c deployer_role_arn=${deployer_role} "
      log_info "Will grant EKS cluster-admin to: ${deployer_role}"
    fi
  fi

  # Deploy all stacks
  log_info "Deploying all CDK stacks..."
  cdk deploy --all --require-approval never ${cdk_context}

  log_info "CDK stacks deployed successfully"

  cd "${REPO_ROOT}"
}

# ===========================================================================
# ECR Repository Management (replaces CDK ECR stack)
# ===========================================================================
create_ecr_repos() {
  log_info "Creating ECR repositories..."

  local region="${AWS_REGION:-${AWS_DEFAULT_REGION}}"

  # Platform image repos
  local repos=(
    "${PROJECT_NAME}-platform"
    "${PROJECT_NAME}-metrics-exporter"
    "${PROJECT_NAME}-billing-consumer"
  )

  for repo in "${repos[@]}"; do
    if aws ecr describe-repositories --repository-names "${repo}" --region "${region}" &>/dev/null; then
      log_info "ECR repo ${repo} already exists"
    else
      aws ecr create-repository \
        --repository-name "${repo}" \
        --image-scanning-configuration scanOnPush=true \
        --region "${region}"
      log_info "Created ECR repo: ${repo}"
    fi
  done

  # Agent / operator mirror repos (for CN regions)
  local mirror_repos=(
    "openclaw/openclaw"
    "nginx"
    "astral-sh/uv"
    "otel/opentelemetry-collector"
    "openclaw-rocks/openclaw-operator"
    "eks/aws-load-balancer-controller"
  )

  for repo in "${mirror_repos[@]}"; do
    if aws ecr describe-repositories --repository-names "${repo}" --region "${region}" &>/dev/null; then
      log_info "ECR mirror repo ${repo} already exists"
    else
      aws ecr create-repository \
        --repository-name "${repo}" \
        --image-scanning-configuration scanOnPush=false \
        --region "${region}" 2>/dev/null || true
      log_info "Created ECR mirror repo: ${repo}"
    fi
  done

  # Helm chart repos (for CN regions)
  local chart_repos=(
    "charts/aws-load-balancer-controller"
    "charts/openclaw-operator"
  )

  for repo in "${chart_repos[@]}"; do
    if aws ecr describe-repositories --repository-names "${repo}" --region "${region}" &>/dev/null; then
      log_info "ECR chart repo ${repo} already exists"
    else
      aws ecr create-repository \
        --repository-name "${repo}" \
        --image-scanning-configuration scanOnPush=false \
        --region "${region}" 2>/dev/null || true
      log_info "Created ECR chart repo: ${repo}"
    fi
  done

  # Derive ECR registry from account + region
  local account_id="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text)}"
  local suffix=""
  [[ "${region}" == cn-* ]] && suffix=".cn"
  ECR_REGISTRY="${account_id}.dkr.ecr.${region}.amazonaws.com${suffix}"

  log_info "ECR registry: ${ECR_REGISTRY}"
}

configure_kubectl() {
  log_info "Configuring kubectl..."

  local cluster_name=$(get_stack_output "${STACK_PREFIX}-eks" "ClusterName")

  if [[ -z "${cluster_name}" ]]; then
    log_error "Could not find EKS cluster name in stack outputs"
  fi

  log_info "Updating kubeconfig for cluster: ${cluster_name}"
  aws eks update-kubeconfig --name "${cluster_name}" --region "${AWS_REGION}"

  # Ensure current identity has cluster access (idempotent)
  local caller_arn=$(aws sts get-caller-identity --query 'Arn' --output text 2>/dev/null || echo "")
  if [[ -n "${caller_arn}" ]]; then
    # For assumed-role, convert to role ARN; for IAM user, use as-is
    local principal_arn="${caller_arn}"
    if [[ "${caller_arn}" == *":assumed-role/"* ]]; then
      principal_arn=$(echo "${caller_arn}" | sed 's|:assumed-role/|:role/|; s|/i-[^/]*$||; s|/[^/]*$||')
    fi
    log_info "Ensuring EKS access for: ${principal_arn}"
    aws eks create-access-entry \
      --cluster-name "${cluster_name}" \
      --principal-arn "${principal_arn}" \
      --type STANDARD \
      --region "${AWS_REGION}" 2>/dev/null || true
    aws eks associate-access-policy \
      --cluster-name "${cluster_name}" \
      --principal-arn "${principal_arn}" \
      --policy-arn "arn:${AWS_PARTITION:-aws}:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy" \
      --access-scope type=cluster \
      --region "${AWS_REGION}" 2>/dev/null || true
  fi

  # Wait for cluster to be ready
  log_info "Waiting for nodes to be ready..."
  kubectl wait --for=condition=Ready nodes --all --timeout=300s || true

  log_info "kubectl configured successfully"
}

ensure_storage_class() {
  log_info "Ensuring gp3 StorageClass exists..."

  kubectl apply -f "${REPO_ROOT}/k8s-platform/storage/storageclass.yaml"

  log_info "StorageClass gp3 ready"
}

install_alb_controller() {
  log_info "Installing AWS Load Balancer Controller..."

  local cluster_name=$(get_stack_output "${STACK_PREFIX}-eks" "ClusterName")
  local vpc_id=$(get_stack_output "${STACK_PREFIX}-vpc" "VpcId")
  local region="${AWS_REGION}"

  local helm_args=(
    --set clusterName="${cluster_name}"
    --set serviceAccount.create=true
    --set region="${region}"
    --set vpcId="${vpc_id}"
  )

  # Configure IRSA: ALB Controller needs its own IAM role, not the node role
  local alb_role_arn=$(get_stack_output "${STACK_PREFIX}-iam" "ALBControllerRoleArn")
  if [[ -n "${alb_role_arn}" ]]; then
    helm_args+=(--set "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn=${alb_role_arn}")
    log_info "Using IRSA role for ALB Controller: ${alb_role_arn}"
  fi

  if [[ -n "${ALB_CONTROLLER_IMAGE:-}" ]]; then
    helm_args+=(--set "image.repository=${ECR_REGISTRY}/${ALB_CONTROLLER_IMAGE}")
    if [[ -n "${ALB_CONTROLLER_TAG:-}" ]]; then
      helm_args+=(--set "image.tag=${ALB_CONTROLLER_TAG}")
    fi
    log_info "Using mirrored ALB controller image: ${ECR_REGISTRY}/${ALB_CONTROLLER_IMAGE}:${ALB_CONTROLLER_TAG:-latest}"
  fi

  # Login to ECR for Helm OCI
  aws ecr get-login-password --region "${region}" | helm registry login --username AWS --password-stdin "${ECR_REGISTRY}"

  # Use mirrored chart from CN ECR if available, otherwise try upstream sources
  local alb_chart_version="${ALB_CONTROLLER_CHART_VERSION:-3.2.1}"
  local chart_ref="oci://${ECR_REGISTRY}/charts/aws-load-balancer-controller"

  if helm upgrade --install aws-load-balancer-controller "${chart_ref}" \
    -n kube-system --version "${alb_chart_version}" \
    "${helm_args[@]}" 2>/dev/null; then
    log_info "AWS Load Balancer Controller installed (from CN ECR)"
    return
  fi

  log_warn "CN ECR chart not found, trying public.ecr.aws..."
  if helm upgrade --install aws-load-balancer-controller \
    oci://public.ecr.aws/eks/aws-load-balancer-controller \
    -n kube-system --version "${alb_chart_version}" \
    "${helm_args[@]}" 2>/dev/null; then
    log_info "AWS Load Balancer Controller installed (from public ECR)"
    return
  fi

  log_warn "OCI sources failed, trying GitHub Helm repo..."
  helm repo add eks https://aws.github.io/eks-charts 2>/dev/null || true
  helm repo update
  helm upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
    -n kube-system "${helm_args[@]}"

  log_info "AWS Load Balancer Controller installed"
}

install_openclaw_operator() {
  log_info "Installing openclaw-operator..."

  local operator_version="${OPERATOR_VERSION:-0.26.2}"
  local helm_set_args=(
    --set leaderElection.enabled=true
    --set crds.install=true
  )

  if [[ -n "${OPERATOR_IMAGE_REPO:-}" ]]; then
    helm_set_args+=(
      --set "image.repository=${ECR_REGISTRY}/${OPERATOR_IMAGE_REPO}"
      --set "image.tag=v${operator_version}"
    )
    log_info "Using mirrored operator image: ${ECR_REGISTRY}/${OPERATOR_IMAGE_REPO}:v${operator_version}"
  fi

  # Login to ECR for Helm OCI
  local region="${AWS_REGION}"
  aws ecr get-login-password --region "${region}" | helm registry login --username AWS --password-stdin "${ECR_REGISTRY}" 2>/dev/null || true

  # Try mirrored chart from CN ECR first
  local chart_ref="oci://${ECR_REGISTRY}/charts/openclaw-operator"
  if helm upgrade --install openclaw-operator "${chart_ref}" \
    --namespace openclaw-operator-system --create-namespace \
    --version "${operator_version}" \
    "${helm_set_args[@]}" 2>/dev/null; then
    log_info "openclaw-operator v${operator_version} installed (from CN ECR)"
    return
  fi

  log_warn "CN ECR chart not found, trying ghcr.io..."
  helm upgrade --install openclaw-operator \
    oci://ghcr.io/openclaw-rocks/charts/openclaw-operator \
    --namespace openclaw-operator-system --create-namespace \
    --version "${operator_version}" \
    "${helm_set_args[@]}"

  log_info "openclaw-operator v${operator_version} installed"
}

create_platform_secret() {
  log_info "Creating platform-api-config secret..."

  # Ensure namespace exists
  kubectl create ns openclaw-platform --dry-run=client -o yaml | kubectl apply -f - 2>/dev/null

  # Validate required variables
  local missing=()
  [[ -z "${ADMIN_EMAIL:-}" ]] && missing+=("ADMIN_EMAIL")
  [[ -z "${ADMIN_PASSWORD:-}" ]] && missing+=("ADMIN_PASSWORD")
  [[ -z "${JWT_SECRET:-}" ]] && missing+=("JWT_SECRET")
  [[ -z "${AWS_REGION:-}" ]] && missing+=("AWS_REGION")
  [[ -z "${AWS_PARTITION:-}" ]] && missing+=("AWS_PARTITION")
  [[ -z "${AWS_ACCOUNT_ID:-}" ]] && missing+=("AWS_ACCOUNT_ID")
  [[ -z "${METRICS_EXPORTER_REPO:-}" ]] && missing+=("METRICS_EXPORTER_REPO")
  [[ -z "${METRICS_EXPORTER_TAG:-}" ]] && missing+=("METRICS_EXPORTER_TAG")
  if [[ ${#missing[@]} -gt 0 ]]; then
    log_error "Required variables not set: ${missing[*]}. Please configure them in .env"
  fi

  # Auto-populate from CDK/CF outputs
  local db_secret_arn=$(get_stack_output "${STACK_PREFIX}-rds" "RDSSecretArn")
  local db_endpoint=$(get_stack_output "${STACK_PREFIX}-rds" "RDSEndpoint")
  local db_port=$(get_stack_output "${STACK_PREFIX}-rds" "RDSPort")
  local db_name=$(get_stack_output "${STACK_PREFIX}-rds" "DbName")
  local queue_url=$(get_stack_output "${STACK_PREFIX}-sqs" "UsageQueueUrl")

  # CF mode: SQS output key is different
  if [[ -z "${queue_url}" ]]; then
    queue_url=$(get_stack_output "${STACK_PREFIX}-sqs" "SQSQueueUrl")
  fi

  # CF mode: port and db_name may not be in outputs, use defaults
  [[ -z "${db_port}" ]] && db_port="5432"
  [[ -z "${db_name}" ]] && db_name="openclawsaas"

  # Read metrics-exporter version from VERSION file if not set
  if [[ -z "${METRICS_EXPORTER_TAG:-}" ]]; then
    local me_version_file="${REPO_ROOT}/../platform/metrics-exporter/VERSION"
    if [[ -f "${me_version_file}" ]]; then
      METRICS_EXPORTER_TAG="v$(cat "${me_version_file}" | tr -d '[:space:]')"
      log_info "Read metrics-exporter version from VERSION file: ${METRICS_EXPORTER_TAG}"
    fi
  fi

  # Get DB credentials from Secrets Manager
  local db_secret=$(aws secretsmanager get-secret-value --secret-id "${db_secret_arn}" --query SecretString --output text)
  local db_username=$(echo "${db_secret}" | jq -r .username)
  local db_password=$(echo "${db_secret}" | jq -r .password)

  # Create database URL (asyncpg driver)
  local database_url="postgresql+asyncpg://${db_username}:${db_password}@${db_endpoint}:${db_port}/${db_name}"

  # Compose full agent image URI
  local agent_image="${DEFAULT_AGENT_IMAGE:-}"
  if [[ -n "${agent_image}" && ! "${agent_image}" == *"."* ]]; then
    agent_image="${ECR_REGISTRY}/${agent_image}"
  fi

  # Create or update secret
  kubectl create secret generic platform-api-config \
    -n openclaw-platform \
    --from-literal=DATABASE_URL="${database_url}" \
    --from-literal=SQS_QUEUE_URL="${queue_url}" \
    --from-literal=ADMIN_EMAIL="${ADMIN_EMAIL}" \
    --from-literal=ADMIN_PASSWORD="${ADMIN_PASSWORD}" \
    --from-literal=JWT_SECRET="${JWT_SECRET}" \
    --from-literal=K8S_IN_CLUSTER="${K8S_IN_CLUSTER:-true}" \
    --from-literal=LOG_LEVEL="${LOG_LEVEL:-INFO}" \
    --from-literal=AWS_REGION="${AWS_REGION}" \
    --from-literal=AWS_PARTITION="${AWS_PARTITION:-aws}" \
    --from-literal=AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID}" \
    --from-literal=ECR_REGISTRY="${ECR_REGISTRY:-}" \
    --from-literal=AVAILABLE_CHANNELS="${AVAILABLE_CHANNELS:-feishu}" \
    --from-literal=DEFAULT_AGENT_IMAGE="${agent_image}" \
    --from-literal=DEFAULT_AGENT_IMAGE_TAG="${DEFAULT_AGENT_IMAGE_TAG:-latest}" \
    --from-literal=METRICS_EXPORTER_REPO="${METRICS_EXPORTER_REPO}" \
    --from-literal=METRICS_EXPORTER_TAG="${METRICS_EXPORTER_TAG}" \
    --dry-run=client -o yaml | kubectl apply -f -

  log_info "platform-api-config secret created"
}

deploy_platform_api() {
  log_info "Deploying platform API..."

  local platform_repo="${PROJECT_NAME}-platform"

  # Determine image version
  if [[ -z "${PLATFORM_VERSION}" ]]; then
    log_info "No version specified, using latest from ECR..."
    PLATFORM_VERSION=$(aws ecr describe-images \
      --repository-name "${platform_repo}" \
      --query 'sort_by(imageDetails,& imagePushedAt)[-1].imageTags[0]' \
      --output text 2>/dev/null || echo "latest")
  fi

  local platform_image="${ECR_REGISTRY}/${platform_repo}:${PLATFORM_VERSION}"
  log_info "Using platform image: ${platform_image}"

  # Apply K8s manifests with substitution
  export PLATFORM_IMAGE="${platform_image}"
  export ACM_CERT_ARN="${ACM_CERT_ARN:-none}"
  export DOMAIN_NAME="${DOMAIN_NAME:-*}"

  # NLB configuration: use pre-created SG from CF or CDK stack
  local nlb_sg=$(get_stack_output "${STACK_PREFIX}-eks" "PlatformNLBSecurityGroupId")
  if [[ -n "${nlb_sg}" ]]; then
    export SERVICE_TYPE="LoadBalancer"
    export NLB_SECURITY_GROUP_ID="${nlb_sg}"
    log_info "Using NLB with pre-created SG: ${nlb_sg}"
  else
    export SERVICE_TYPE="${SERVICE_TYPE:-ClusterIP}"
    export NLB_SECURITY_GROUP_ID=""
    log_info "Using service type: ${SERVICE_TYPE} (no NLB SG found in stack outputs)"
  fi

  cd "${REPO_ROOT}/k8s/platform"
  bash apply.sh

  log_info "Platform API deployed"
}

deploy_billing_consumer() {
  log_info "Deploying billing consumer..."

  local billing_repo="${BILLING_CONSUMER_REPO:-${PROJECT_NAME}-billing-consumer}"
  local billing_tag="${BILLING_CONSUMER_TAG:-v0.1.1}"
  local billing_image="${ECR_REGISTRY}/${billing_repo}:${billing_tag}"

  log_info "Using billing image: ${billing_image}"

  export BILLING_IMAGE="${billing_image}"
  export AWS_REGION="${AWS_REGION}"

  envsubst < "${REPO_ROOT}/k8s-platform/billing-consumer.yaml" | kubectl apply -f -

  log_info "Billing consumer deployed"
}

verify_deployment() {
  log_info "Verifying deployment..."

  local pod_name=$(kubectl get pod -l app=platform-api -n openclaw-platform -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

  if [[ -z "${pod_name}" ]]; then
    log_warn "No platform-api pod found yet"
  else
    log_info "Platform API pod: ${pod_name}"
    kubectl get pod -n openclaw-platform "${pod_name}"
  fi

  # Check for NLB endpoint
  local nlb_host=$(kubectl get svc platform-api -n openclaw-platform -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "")

  if [[ -n "${nlb_host}" ]]; then
    log_info "Platform API accessible at: http://${nlb_host}:8890"
  else
    local ingress_host=$(kubectl get ingress platform-ingress -n openclaw-platform -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "")
    if [[ -n "${ingress_host}" ]]; then
      log_info "Platform API accessible at: https://${ingress_host}"
    else
      log_warn "No external endpoint yet. Use: kubectl port-forward -n openclaw-platform svc/platform-api 8890:8890"
    fi
  fi

  log_info "Deployment verified!"
}

main() {
  log_info "Starting OpenClaw SaaS deployment..."

  check_prerequisites

  # Get config from cdk.json
  PROJECT_NAME=$(jq -r '.context.project_name' "${REPO_ROOT}/cdk/cdk.json")
  ENVIRONMENT=$(jq -r '.context.environment' "${REPO_ROOT}/cdk/cdk.json")
  if [[ -n "${ENVIRONMENT}" && "${ENVIRONMENT}" != "null" ]]; then
    STACK_PREFIX="${PROJECT_NAME}-${ENVIRONMENT}"
  else
    STACK_PREFIX="${PROJECT_NAME}"
  fi

  log_info "Project: ${PROJECT_NAME}, Environment: ${ENVIRONMENT:-<none>}, Stack prefix: ${STACK_PREFIX}"

  if [[ "${SKIP_CDK}" == "false" ]]; then
    deploy_cdk
  else
    log_warn "Skipping CDK deployment (--skip-cdk)"
  fi

  # Create ECR repos (always, idempotent)
  create_ecr_repos

  if [[ "${SKIP_K8S}" == "false" ]]; then
    configure_kubectl
    ensure_storage_class
    install_alb_controller
    install_openclaw_operator
    create_platform_secret
    deploy_platform_api
    deploy_billing_consumer
    verify_deployment
  else
    log_warn "Skipping Kubernetes deployment (--skip-k8s)"
  fi

  log_info "Deployment complete!"
  echo ""
  log_info "Next steps:"
  echo "  1. Check pods:    kubectl get pods -n openclaw-platform"
  echo "  2. Check ingress: kubectl get ingress -n openclaw-platform"
  echo "  3. View logs:     kubectl logs -n openclaw-platform -l app=platform-api -f"
}

main "$@"
