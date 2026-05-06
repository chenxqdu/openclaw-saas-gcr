#!/usr/bin/env bash
set -euo pipefail

# Script to apply K8s platform manifests with variable substitution
# Usage: ./apply.sh
#
# Required environment variables:
#   PLATFORM_IMAGE    - Platform API container image (e.g., <ECR_REGISTRY>/openclaw-saas-platform:<VERSION>)
#   ACM_CERT_ARN      - ACM certificate ARN for ALB ingress
#   DOMAIN_NAME       - Custom domain name for ingress

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Validate required variables
: "${PLATFORM_IMAGE:?PLATFORM_IMAGE is required - set to the full ECR image URI with tag}"
: "${ACM_CERT_ARN:?ACM_CERT_ARN is required - set to the ACM certificate ARN for ALB ingress}"
: "${DOMAIN_NAME:?DOMAIN_NAME is required - set to the custom domain name or '*' for default}"

echo "==> Applying OpenClaw platform manifests..."
echo "    Platform Image: ${PLATFORM_IMAGE}"
echo "    ACM Cert ARN:   ${ACM_CERT_ARN}"
echo "    Domain Name:    ${DOMAIN_NAME}"

# Apply manifests with variable substitution
MANIFESTS=(namespace.yaml rbac.yaml service.yaml deployment.yaml)

# Only apply ingress if a real ACM cert and domain are configured
if [[ "${ACM_CERT_ARN}" != "none" && "${DOMAIN_NAME}" != "*" ]]; then
  MANIFESTS+=(ingress.yaml)
else
  echo "    Skipping ingress.yaml (no custom domain configured)"
fi

FAILED=()

for manifest in "${MANIFESTS[@]}"; do
  if [[ ! -f "${SCRIPT_DIR}/${manifest}" ]]; then
    echo "    WARNING: ${manifest} not found, skipping"
    continue
  fi
  echo "    Applying ${manifest}..."
  if ! envsubst < "${SCRIPT_DIR}/${manifest}" | kubectl apply -f -; then
    echo "    ERROR: Failed to apply ${manifest}"
    FAILED+=("${manifest}")
  fi
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo ""
  echo "==> ERROR: Failed to apply: ${FAILED[*]}"
  exit 1
fi

echo "==> Platform manifests applied successfully!"
echo ""
echo "    To check status:"
echo "      kubectl get pods -n openclaw-platform"
echo "      kubectl get ingress -n openclaw-platform"
