#!/usr/bin/env bash
set -euo pipefail

# Apply K8s platform manifests (raw kubectl, no Helm).
# For production use, prefer the Helm chart at infra/helm/openclaw-platform/.
#
# Required env:
#   PLATFORM_IMAGE        - e.g. public.ecr.aws/bingjiao/openclaw-saas-platform:v0.9.55
#
# Optional env — networking:
#   ACM_CERT_ARN          - ACM certificate ARN (ALB mode)
#   DOMAIN_NAME           - Custom domain (ALB mode)
#   NLB_SG_PREFIX_LISTS   - Prefix list IDs for NLB SG (empty = allow all)
#
# Optional env — database:
#   DB_MODE               - "postgres" (default) or "sqlite"
#   SQLITE_STORAGE_CLASS  - StorageClass for SQLite PVC (default: gp3)
#   SQLITE_PVC_SIZE       - PVC size (default: 10Gi)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

: "${PLATFORM_IMAGE:?PLATFORM_IMAGE is required}"

DB_MODE="${DB_MODE:-postgres}"
SQLITE_STORAGE_CLASS="${SQLITE_STORAGE_CLASS:-gp3}"
SQLITE_PVC_SIZE="${SQLITE_PVC_SIZE:-10Gi}"

if [[ -n "${ACM_CERT_ARN:-}" && -n "${DOMAIN_NAME:-}" ]]; then
  DEPLOY_MODE="alb"
  echo "==> Networking: ALB + custom domain (${DOMAIN_NAME})"
else
  DEPLOY_MODE="nlb"
  echo "==> Networking: NLB (LB Controller managed)"
fi
echo "    Image:    ${PLATFORM_IMAGE}"
echo "    Database: ${DB_MODE}"

# SQLite: create PVC before deployment
if [[ "${DB_MODE}" == "sqlite" ]]; then
  echo "    Creating PVC for SQLite (${SQLITE_PVC_SIZE} on ${SQLITE_STORAGE_CLASS})..."
  cat <<PVCEOF | kubectl apply -f -
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: platform-data
  namespace: openclaw-platform
spec:
  accessModes: ["ReadWriteOnce"]
  storageClassName: "${SQLITE_STORAGE_CLASS}"
  resources:
    requests:
      storage: ${SQLITE_PVC_SIZE}
PVCEOF
fi

# Apply namespace + rbac + service
for manifest in namespace.yaml rbac.yaml; do
  echo "    Applying ${manifest}..."
  envsubst < "${SCRIPT_DIR}/${manifest}" | kubectl apply -f -
done

# service.yaml: strip prefix-list annotation when empty
echo "    Applying service.yaml..."
if [[ -z "${NLB_SG_PREFIX_LISTS:-}" ]]; then
  envsubst < "${SCRIPT_DIR}/service.yaml" | \
    sed '/aws-load-balancer-security-group-prefix-lists/d' | \
    kubectl apply -f -
else
  envsubst < "${SCRIPT_DIR}/service.yaml" | kubectl apply -f -
fi

# deployment.yaml: envsubst + sqlite volume replacement
echo "    Applying deployment.yaml (DB_MODE=${DB_MODE})..."
if [[ "${DB_MODE}" == "sqlite" ]]; then
  envsubst < "${SCRIPT_DIR}/deployment.yaml" | \
    sed -e 's|emptyDir: {}|persistentVolumeClaim:\n            claimName: platform-data|' \
        -e 's|mountPath: /app/data|mountPath: /data|g' | \
    kubectl apply -f -
else
  envsubst < "${SCRIPT_DIR}/deployment.yaml" | kubectl apply -f -
fi

if [[ "${DEPLOY_MODE}" == "alb" ]]; then
  echo "    Applying ingress.yaml..."
  envsubst < "${SCRIPT_DIR}/ingress.yaml" | kubectl apply -f -
fi

echo "==> Platform manifests applied successfully!"

# Wait for endpoint
if [[ "${DEPLOY_MODE}" == "nlb" ]]; then
  echo "==> Waiting for NLB endpoint..."
  for i in $(seq 1 36); do
    LB_DNS=$(kubectl get svc platform-api -n openclaw-platform \
      -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)
    [[ -n "$LB_DNS" ]] && echo "    Endpoint: http://${LB_DNS}" && break
    echo "    Waiting... (${i}/36)"; sleep 5
  done
else
  echo "==> Waiting for ALB endpoint..."
  for i in $(seq 1 36); do
    LB_DNS=$(kubectl get ingress platform-ingress -n openclaw-platform \
      -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || true)
    [[ -n "$LB_DNS" ]] && echo "    Endpoint: http://${LB_DNS}" && break
    echo "    Waiting... (${i}/36)"; sleep 5
  done
fi
