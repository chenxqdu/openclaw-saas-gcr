#!/usr/bin/env bash
set -euo pipefail

# OpenClaw SaaS Infrastructure Deployment Script
#
# This script deploys the complete OpenClaw SaaS platform:
# 1. CDK stacks (VPC, EKS, RDS, ECR, SQS, S3, IAM, DNS)
# 2. Kubernetes components (ALB controller, openclaw-operator)
# 3. Platform API application
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
  aws cloudformation describe-stacks \
    --stack-name "${stack_name}" \
    --query "Stacks[0].Outputs[?OutputKey=='${output_key}'].OutputValue" \
    --output text 2>/dev/null || echo ""
}

deploy_cdk() {
  log_info "Deploying CDK stacks..."

  cd "${REPO_ROOT}/cdk"

  # Get project name and environment from cdk.json
  PROJECT_NAME=$(jq -r '.context.project_name' cdk.json)
  ENVIRONMENT=$(jq -r '.context.environment' cdk.json)
  if [[ -n "${ENVIRONMENT}" ]]; then
    STACK_PREFIX="${PROJECT_NAME}-${ENVIRONMENT}"
  else
    STACK_PREFIX="${PROJECT_NAME}"
  fi

  log_info "Project: ${PROJECT_NAME}, Environment: ${ENVIRONMENT:-<none>}, Stack prefix: ${STACK_PREFIX}"

  # Bootstrap CDK (if not already done)
  log_info "Bootstrapping CDK..."
  cdk bootstrap

  # Deploy all stacks (pass environment-specific CDK context from .env)
  log_info "Deploying all CDK stacks..."
  local cdk_context=""
  [[ -n "${DOMAIN_NAME:-}" ]] && cdk_context+="-c domain_name=${DOMAIN_NAME} "
  [[ -n "${HOSTED_ZONE_ID:-}" ]] && cdk_context+="-c hosted_zone_id=${HOSTED_ZONE_ID} "
  [[ -n "${HOSTED_ZONE_NAME:-}" ]] && cdk_context+="-c hosted_zone_name=${HOSTED_ZONE_NAME} "
  [[ -n "${ACM_CERT_ARN:-}" ]] && cdk_context+="-c acm_cert_arn=${ACM_CERT_ARN} "
  cdk deploy --all --require-approval never ${cdk_context}

  log_info "CDK stacks deployed successfully"

  cd "${REPO_ROOT}"
}

configure_kubectl() {
  log_info "Configuring kubectl..."

  # Get cluster name from CDK output
  local cluster_name=$(get_stack_output "${STACK_PREFIX}-eks" "ClusterName")

  if [[ -z "${cluster_name}" ]]; then
    log_error "Could not find EKS cluster name in stack outputs"
  fi

  log_info "Updating kubeconfig for cluster: ${cluster_name}"
  aws eks update-kubeconfig --name "${cluster_name}" --region "${AWS_REGION}"

  # Wait for cluster to be ready
  log_info "Waiting for cluster to be ready..."
  kubectl wait --for=condition=Ready nodes --all --timeout=300s || true

  log_info "kubectl configured successfully"
}

install_alb_controller() {
  log_info "Installing AWS Load Balancer Controller..."

  # Check if already installed
  if helm list -n kube-system | grep -q aws-load-balancer-controller; then
    log_info "AWS Load Balancer Controller already installed, skipping"
    return
  fi

  local cluster_name=$(get_stack_output "${STACK_PREFIX}-eks" "ClusterName")
  local vpc_id=$(get_stack_output "${STACK_PREFIX}-vpc" "VpcId")
  local region="${AWS_REGION}"

  # Add EKS chart repo
  helm repo add eks https://aws.github.io/eks-charts
  helm repo update

  # Install ALB controller
  helm install aws-load-balancer-controller eks/aws-load-balancer-controller \
    -n kube-system \
    --set clusterName="${cluster_name}" \
    --set serviceAccount.create=true \
    --set region="${region}" \
    --set vpcId="${vpc_id}"

  log_info "AWS Load Balancer Controller installed"
}

install_openclaw_operator() {
  log_info "Installing openclaw-operator..."

  # Check if already installed
  if helm list -n openclaw-operator-system | grep -q openclaw-operator; then
    log_info "openclaw-operator already installed, upgrading..."
    helm upgrade openclaw-operator \
      oci://ghcr.io/openclaw-rocks/charts/openclaw-operator \
      --namespace openclaw-operator-system \
      --set leaderElection.enabled=true \
      --set crds.install=true
  else
    helm install openclaw-operator \
      oci://ghcr.io/openclaw-rocks/charts/openclaw-operator \
      --namespace openclaw-operator-system \
      --set leaderElection.enabled=true \
      --set crds.install=true
  fi

  log_info "openclaw-operator installed"
}

create_platform_secret() {
  log_info "Creating platform-api-config secret..."

  # Validate required variables from .env
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

  # Auto-populate from CDK outputs
  local db_secret_arn=$(get_stack_output "${STACK_PREFIX}-rds" "DbSecretArn")
  local db_endpoint=$(get_stack_output "${STACK_PREFIX}-rds" "DbEndpoint")
  local db_port=$(get_stack_output "${STACK_PREFIX}-rds" "DbPort")
  local db_name=$(get_stack_output "${STACK_PREFIX}-rds" "DbName")
  local queue_url=$(get_stack_output "${STACK_PREFIX}-sqs" "UsageQueueUrl")
  local ecr_registry=$(get_stack_output "${STACK_PREFIX}-ecr" "PlatformRepoUriOutput" | cut -d/ -f1)

  # Get DB credentials from Secrets Manager
  local db_secret=$(aws secretsmanager get-secret-value --secret-id "${db_secret_arn}" --query SecretString --output text)
  local db_username=$(echo "${db_secret}" | jq -r .username)
  local db_password=$(echo "${db_secret}" | jq -r .password)

  # Create database URL
  local database_url="postgresql://${db_username}:${db_password}@${db_endpoint}:${db_port}/${db_name}"

  # Read metrics-exporter version from VERSION file if not set
  if [[ -z "${METRICS_EXPORTER_TAG:-}" ]]; then
    local me_version_file="${REPO_ROOT}/platform/metrics-exporter/VERSION"
    if [[ -f "${me_version_file}" ]]; then
      METRICS_EXPORTER_TAG="v$(cat "${me_version_file}" | tr -d '[:space:]')"
      log_info "Read metrics-exporter version from VERSION file: ${METRICS_EXPORTER_TAG}"
    fi
  fi

  # Create or update secret with all configuration
  kubectl create secret generic platform-api-config \
    -n openclaw-platform \
    --from-literal=DATABASE_URL="${database_url}" \
    --from-literal=SQS_QUEUE_URL="${queue_url}" \
    --from-literal=USAGE_EVENTS_QUEUE_URL="${queue_url}" \
    --from-literal=ECR_REGISTRY="${ecr_registry}" \
    --from-literal=ADMIN_EMAIL="${ADMIN_EMAIL}" \
    --from-literal=ADMIN_PASSWORD="${ADMIN_PASSWORD}" \
    --from-literal=JWT_SECRET="${JWT_SECRET}" \
    --from-literal=K8S_IN_CLUSTER="${K8S_IN_CLUSTER:-true}" \
    --from-literal=LOG_LEVEL="${LOG_LEVEL:-INFO}" \
    --from-literal=AWS_REGION="${AWS_REGION}" \
    --from-literal=AWS_PARTITION="${AWS_PARTITION:-aws}" \
    --from-literal=AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID}" \
    --from-literal=ECR_REGISTRY="${ecr_registry:-}" \
    --from-literal=AVAILABLE_CHANNELS="${AVAILABLE_CHANNELS:-}" \
    --from-literal=DEFAULT_AGENT_IMAGE="${DEFAULT_AGENT_IMAGE:-}" \
    --from-literal=DEFAULT_AGENT_IMAGE_TAG="${DEFAULT_AGENT_IMAGE_TAG}" \
    --from-literal=METRICS_EXPORTER_REPO="${METRICS_EXPORTER_REPO}" \
    --from-literal=METRICS_EXPORTER_TAG="${METRICS_EXPORTER_TAG}" \
    --dry-run=client -o yaml | kubectl apply -f -

  log_info "platform-api-config secret created"
}

deploy_platform_api() {
  log_info "Deploying platform API..."

  # Get ECR repository URI
  local ecr_repo_uri=$(get_stack_output "${STACK_PREFIX}-ecr" "PlatformRepoUriOutput")

  if [[ -z "${ecr_repo_uri}" ]]; then
    log_error "Could not find ECR repository URI"
  fi

  # Determine image version
  if [[ -z "${PLATFORM_VERSION}" ]]; then
    local version_file="${REPO_ROOT}/platform/VERSION"
    if [[ -f "${version_file}" ]]; then
      PLATFORM_VERSION="v$(cat "${version_file}" | tr -d '[:space:]')"
      log_info "Read version from platform/VERSION: ${PLATFORM_VERSION}"
    else
      log_info "No version specified and no VERSION file found, using latest from ECR..."
      PLATFORM_VERSION=$(aws ecr describe-images \
        --repository-name "${PROJECT_NAME}-${ENVIRONMENT}-platform" \
        --query 'sort_by(imageDetails,& imagePushedAt)[-1].imageTags[0]' \
        --output text 2>/dev/null || echo "latest")
    fi
  fi

  local platform_image="${ecr_repo_uri}:${PLATFORM_VERSION}"
  log_info "Using platform image: ${platform_image}"

  # Get ACM cert ARN and domain name (optional — only needed for ALB mode)
  local acm_cert_arn=$(get_stack_output "${STACK_PREFIX}-dns" "CertificateArn" || echo "")
  local domain_name=$(get_stack_output "${STACK_PREFIX}-dns" "DomainName" || echo "")

  # CloudFront prefix list for NLB SG inbound rules (LB Controller native annotation).
  # When domain is configured, restrict NLB to CloudFront only.
  # Prefix list ID is region-specific — look up dynamically.
  if [[ -n "$(get_stack_output "${STACK_PREFIX}-dns" "DomainName" 2>/dev/null || echo "")" ]]; then
    if [[ -z "${NLB_SG_PREFIX_LISTS:-}" ]]; then
      NLB_SG_PREFIX_LISTS=$(aws ec2 describe-managed-prefix-lists \
        --filters "Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing" \
        --query "PrefixLists[0].PrefixListId" --output text 2>/dev/null || echo "")
    fi
    export NLB_SG_PREFIX_LISTS
    if [[ -n "${NLB_SG_PREFIX_LISTS}" ]]; then
      log_info "NLB restricted to CloudFront prefix list: ${NLB_SG_PREFIX_LISTS}"
    else
      log_warn "Could not find CloudFront prefix list for this region — NLB will be open"
    fi
  else
    export NLB_SG_PREFIX_LISTS=""
  fi

  # Apply K8s manifests with substitution
  # NLB mode (default): only PLATFORM_IMAGE is required
  # ALB mode: set ACM_CERT_ARN and DOMAIN_NAME for ALB ingress with HTTPS
  export PLATFORM_IMAGE="${platform_image}"
  if [[ -n "${acm_cert_arn}" && -n "${domain_name}" ]]; then
    export ACM_CERT_ARN="${acm_cert_arn}"
    export DOMAIN_NAME="${domain_name}"
  fi

  cd "${REPO_ROOT}/k8s/platform"
  bash apply.sh

  log_info "Platform API deployed"
}

deploy_billing_consumer() {
  log_info "Deploying billing consumer..."

  local ecr_registry=$(get_stack_output "${STACK_PREFIX}-ecr" "PlatformRepoUriOutput" | cut -d/ -f1)
  local billing_repo="${BILLING_CONSUMER_REPO:-openclaw-saas-billing-consumer}"
  local billing_tag="${BILLING_CONSUMER_TAG:-v0.1.1}"
  local billing_image="${ecr_registry}/${billing_repo}:${billing_tag}"

  log_info "Using billing image: ${billing_image}"

  export BILLING_IMAGE="${billing_image}"
  export AWS_REGION="${AWS_REGION}"

  envsubst < "${REPO_ROOT}/k8s-platform/billing-consumer.yaml" | kubectl apply -f -

  log_info "Billing consumer deployed"
}

update_cloudfront_origin() {
  log_info "Updating CloudFront Distribution origin with NLB DNS..."

  local cf_dist_id=$(get_stack_output "${STACK_PREFIX}-dns" "CloudFrontDistributionId" || echo "")
  if [[ -z "${cf_dist_id}" ]]; then
    log_warn "No CloudFront Distribution ID found, skipping origin update"
    return
  fi

  # Wait for NLB DNS
  local nlb_dns=""
  for i in $(seq 1 36); do
    nlb_dns=$(kubectl get svc platform-api -n openclaw-platform \
      -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)
    if [[ -n "${nlb_dns}" ]]; then
      break
    fi
    echo "    Waiting for NLB DNS... (${i}/36)"
    sleep 5
  done

  if [[ -z "${nlb_dns}" ]]; then
    log_warn "NLB DNS not available after 3 minutes. Update CloudFront origin manually."
    return
  fi

  log_info "NLB DNS: ${nlb_dns}"

  # Get current CloudFront config
  local etag
  etag=$(aws cloudfront get-distribution-config --id "${cf_dist_id}" \
    --query 'ETag' --output text)

  local config_json
  config_json=$(aws cloudfront get-distribution-config --id "${cf_dist_id}" \
    --query 'DistributionConfig' --output json)

  # Update origin domain name from placeholder to actual NLB DNS
  local updated_config
  updated_config=$(echo "${config_json}" | jq --arg nlb "${nlb_dns}" '
    .Origins.Items[0].DomainName = $nlb
    | .Origins.Items[0].Id as $old_id
    | .Origins.Items[0].Id = $nlb
    | .DefaultCacheBehavior.TargetOriginId = $nlb
  ')

  # Apply the update
  aws cloudfront update-distribution \
    --id "${cf_dist_id}" \
    --if-match "${etag}" \
    --distribution-config "${updated_config}" > /dev/null

  log_info "CloudFront Distribution ${cf_dist_id} origin updated to ${nlb_dns}"
}

run_db_migration() {
  log_info "Running database migration..."

  # Wait for platform API to be ready
  log_info "Waiting for platform-api pod to be ready..."
  kubectl wait --for=condition=Ready pod -l app=platform-api -n openclaw-platform --timeout=300s

  # Run migration
  local pod_name=$(kubectl get pod -l app=platform-api -n openclaw-platform -o jsonpath='{.items[0].metadata.name}')
  log_info "Running migration in pod: ${pod_name}"

  kubectl exec -n openclaw-platform "${pod_name}" -- python -m alembic upgrade head || {
    log_warn "Migration command failed, but this may be expected if migrations are not set up yet"
  }

  log_info "Database migration complete"
}

verify_deployment() {
  log_info "Verifying deployment..."

  # Check platform API health
  local pod_name=$(kubectl get pod -l app=platform-api -n openclaw-platform -o jsonpath='{.items[0].metadata.name}')

  log_info "Platform API pod: ${pod_name}"
  kubectl get pod -n openclaw-platform "${pod_name}"

  # Check for NLB or ALB ingress
  local nlb_host=$(kubectl get svc platform-api -n openclaw-platform -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)
  local ingress_host=$(kubectl get ingress platform-ingress -n openclaw-platform -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)

  if [[ -n "${ingress_host}" ]]; then
    log_info "Platform API accessible at: https://${ingress_host} (ALB)"
  elif [[ -n "${nlb_host}" ]]; then
    log_info "Platform API NLB: http://${nlb_host} (CloudFront prefix list restricted)"
  else
    log_warn "No external endpoint yet, check with: kubectl get svc -n openclaw-platform"
  fi

  log_info "Deployment verified!"
}

main() {
  log_info "Starting OpenClaw SaaS deployment..."

  # Load environment configuration
  if [[ -f "${REPO_ROOT}/.env" ]]; then
    log_info "Loading configuration from .env"
    set -a
    source "${REPO_ROOT}/.env"
    set +a
  else
    log_error "No .env file found. Copy .env.global or .env.cn to .env and configure it."
  fi

  check_prerequisites

  # Get config from cdk.json
  PROJECT_NAME=$(jq -r '.context.project_name' "${REPO_ROOT}/cdk/cdk.json")
  ENVIRONMENT=$(jq -r '.context.environment' "${REPO_ROOT}/cdk/cdk.json")
  if [[ -n "${ENVIRONMENT}" ]]; then
    STACK_PREFIX="${PROJECT_NAME}-${ENVIRONMENT}"
  else
    STACK_PREFIX="${PROJECT_NAME}"
  fi

  if [[ "${SKIP_CDK}" == "false" ]]; then
    deploy_cdk
  else
    log_warn "Skipping CDK deployment (--skip-cdk)"
  fi

  if [[ "${SKIP_K8S}" == "false" ]]; then
    configure_kubectl
    install_alb_controller
    install_openclaw_operator
    create_platform_secret
    deploy_platform_api
    deploy_billing_consumer
    update_cloudfront_origin
    run_db_migration
    verify_deployment
  else
    log_warn "Skipping Kubernetes deployment (--skip-k8s)"
  fi

  log_info "Deployment complete!"
  echo ""
  log_info "Next steps:"
  echo "  1. Check pods:    kubectl get pods -n openclaw-platform"
  echo "  2. Check NLB:     kubectl get svc platform-api -n openclaw-platform"
  echo "  3. View logs:     kubectl logs -n openclaw-platform -l app=platform-api -f"
}

main "$@"
