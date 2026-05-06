# K8s Secret Template

**DO NOT use this directly.** `deploy.sh` creates this secret automatically from `.env` + CDK outputs.

This is reference-only for manual troubleshooting.

```bash
kubectl create secret generic platform-api-config \
  --namespace openclaw-platform \
  --from-literal=DATABASE_URL="postgresql+asyncpg://user:pass@endpoint:5432/openclawsaas" \
  --from-literal=SQS_QUEUE_URL="https://${AWS_DEFAULT_REGION}.queue.amazonaws.com.cn/${AWS_ACCOUNT_ID}/openclaw-saas-usage-events" \
  --from-literal=USAGE_EVENTS_QUEUE_URL="<same as SQS_QUEUE_URL>" \
  --from-literal=ECR_REGISTRY="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_DEFAULT_REGION}.amazonaws.com.cn" \
  --from-literal=ADMIN_EMAIL="admin@example.com" \
  --from-literal=ADMIN_PASSWORD="<strong-password>" \
  --from-literal=JWT_SECRET="<openssl rand -hex 32>" \
  --from-literal=K8S_IN_CLUSTER="true" \
  --from-literal=LOG_LEVEL="INFO" \
  --from-literal=AWS_REGION="${AWS_DEFAULT_REGION}" \
  --from-literal=AWS_PARTITION="aws-cn" \
  --from-literal=AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID}" \
  --from-literal=AVAILABLE_CHANNELS="feishu" \
  --from-literal=DEFAULT_AGENT_IMAGE="" \
  --from-literal=DEFAULT_AGENT_IMAGE_TAG="latest" \
  --from-literal=METRICS_EXPORTER_REPO="openclaw-saas-metrics-exporter" \
  --from-literal=METRICS_EXPORTER_TAG="v0.3.1" \
  --dry-run=client -o yaml | kubectl apply -f -
```

See `infra/.env.cn` for pre-filled CN values, or `infra/.env.example` for variable descriptions.
