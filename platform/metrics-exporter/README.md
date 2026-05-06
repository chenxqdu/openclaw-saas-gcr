# Metrics Exporter Sidecar

Injected into every Agent Pod. Scrapes OpenClaw metrics from the otel-collector Prometheus endpoint and pushes usage deltas to SQS for billing.

## Architecture

```
openclaw → OTEL SDK → otel-collector → Prometheus (:9090/metrics)
                                              ↓ scrape every 30s
                                     metrics-exporter
                                              ↓ compute delta
                                           SQS Queue
                                              ↓
                                      Billing Consumer
```

## Version

- **Current:** v0.3.0
- **Image:** `openclaw-saas-metrics-exporter:v0.3.0`

## How It Works

1. **Scrape:** Every 30s, `GET http://localhost:9090/metrics` (otel-collector)
2. **Parse:** Extract billing-relevant counters from Prometheus text format
3. **Delta:** Compare with previous scrape values (counters are monotonically increasing)
4. **Push:** Send non-zero deltas to SQS as usage events

## Metrics Tracked

From otel-collector (OpenClaw OTEL SDK):

| Metric | Description |
|--------|-------------|
| `openclaw_tokens_input` | Input tokens consumed |
| `openclaw_tokens_output` | Output tokens generated |
| `openclaw_tokens_cache_read` | Cached input tokens |
| `openclaw_tokens_cache_write` | Tokens written to cache |
| `openclaw_cost_usd` | Cost in USD |
| `openclaw_message_processed` | LLM messages processed |

## SQS Event Format

```json
{
  "tenant": "tenant-abc",
  "agent": "agent-01",
  "model": "global.anthropic.claude-opus-4-6-v1",
  "provider": "amazon-bedrock",
  "input_tokens": 1500,
  "output_tokens": 350,
  "cache_read": 200,
  "cache_write": 0,
  "total_tokens": 1850,
  "cost_usd": 0.0234,
  "messages": 1,
  "timestamp": 1712983200000
}
```

## Configuration (Environment Variables)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TENANT_NAME` | ✅ | | Tenant identifier |
| `AGENT_NAME` | ✅ | | Agent identifier |
| `SQS_QUEUE_URL` | ✅ | | SQS queue URL for usage events |
| `AWS_DEFAULT_REGION` | | `us-west-2` | AWS region for SQS client |
| `SCAN_INTERVAL_SECONDS` | | `30` | Scrape interval |
| `OTEL_METRICS_PORT` | | `9090` | otel-collector Prometheus port |

## Changelog

### v0.3.0 (2026-04-13)
- **Rewrite:** Scrape otel-collector instead of JSONL files
- Removed Prometheus server (was conflicting with otel-collector on :9090)
- Removed `prometheus_client` dependency
- Removed data volume mount (no longer reads files)
- Added startup retry loop for otel-collector readiness
- Added `cost_usd` and `messages` to SQS events

### v0.2.0 (2026-04-13)
- Disabled Prometheus server to fix port conflict

### v0.1.0 (2026-04-12)
- Initial: JSONL file scanning + Prometheus + SQS push
