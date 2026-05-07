"""Metrics exporter: scrapes otel-collector Prometheus endpoint, computes deltas, pushes to SQS."""
import re
import time
import urllib.request
from typing import Dict, Optional
from config import Config
from sqs_pusher import SQSPusher


# openclaw.tokens counter is exported with an `openclaw_token` label whose value
# is one of input/output/cache_read/cache_write/prompt/total. The earlier
# implementation summed across label values, producing a single inflated counter
# and leaving input_tokens/output_tokens permanently at 0 in SQS events. We now
# split by label: `openclaw_tokens_total` + {openclaw_token="input"} rekeys to
# `openclaw_tokens_input` internally.
TOKEN_METRIC_NAMES = {"openclaw_tokens", "openclaw_tokens_total"}

BILLING_METRICS = {
    "openclaw_cost_usd_total",
    "openclaw_cost_usd",
    "openclaw_message_processed_total",
    "openclaw_message_processed",
}

_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')


def _parse_labels(label_str: str) -> Dict[str, str]:
    return {m.group(1): m.group(2) for m in _LABEL_RE.finditer(label_str)}


def parse_prometheus_text(text: str) -> Dict[str, float]:
    """Parse Prometheus text exposition format into {metric_key: value}.

    For openclaw.tokens, split by the `openclaw_token` label so that
    `openclaw_tokens_total{openclaw_token="input"} 18719` becomes
    `openclaw_tokens_input` → 18719 (and similarly for output/cache_read/...).
    For other BILLING_METRICS, sum across label combinations.
    """
    metrics: Dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parts = line.split(None, 1)
            if len(parts) < 2:
                continue
            name_with_labels, rest = parts[0], parts[1]
            value = float(rest.split()[0])

            if "{" in name_with_labels:
                name, label_str = name_with_labels.split("{", 1)
                label_str = label_str.rstrip("}")
            else:
                name, label_str = name_with_labels, ""

            if name in TOKEN_METRIC_NAMES:
                token = _parse_labels(label_str).get("openclaw_token")
                if not token:
                    continue
                key = f"openclaw_tokens_{token}"
                metrics[key] = metrics.get(key, 0.0) + value
            elif name in BILLING_METRICS:
                metrics[name] = metrics.get(name, 0.0) + value
        except (ValueError, IndexError):
            continue
    return metrics


def extract_labels(text: str, metric_prefix: str) -> Dict[str, str]:
    """Extract label values from any metric line matching prefix.

    Returns first match of {model="...", ...} labels.
    """
    for line in text.splitlines():
        if line.startswith(metric_prefix) and "{" in line:
            label_str = line[line.index("{") + 1 : line.index("}")]
            labels = {}
            for part in label_str.split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    labels[k.strip()] = v.strip().strip('"')
            return labels
    return {}


class MetricsExporter:
    """Scrape otel-collector /metrics, compute deltas, push to SQS."""

    def __init__(self):
        self.sqs_pusher = SQSPusher()
        self.prev_values: Dict[str, float] = {}
        self.metrics_url = f"http://localhost:{Config.OTEL_METRICS_PORT}/metrics"

    def scrape(self) -> Optional[str]:
        """Fetch Prometheus metrics from otel-collector."""
        try:
            resp = urllib.request.urlopen(self.metrics_url, timeout=5)
            return resp.read().decode()
        except Exception as e:
            print(f"Scrape error: {e}")
            return None

    def compute_and_push(self, raw_text: str):
        """Compute deltas from previous scrape and push non-zero deltas to SQS."""
        current = parse_prometheus_text(raw_text)
        labels = extract_labels(raw_text, "openclaw_tokens")

        if not current:
            return  # No billing metrics yet

        # Compute deltas
        deltas: Dict[str, float] = {}
        for name, value in current.items():
            prev = self.prev_values.get(name, 0.0)
            delta = value - prev
            if delta > 0:
                deltas[name] = delta

        # Update previous values
        self.prev_values = current

        if not deltas:
            return  # No new usage

        # Normalize metric names: try both with and without _total suffix
        def get_delta(*names):
            for n in names:
                if n in deltas:
                    return deltas[n]
            return 0

        input_tokens = get_delta("openclaw_tokens_input")
        output_tokens = get_delta("openclaw_tokens_output")
        cache_read = get_delta("openclaw_tokens_cache_read")
        cache_write = get_delta("openclaw_tokens_cache_write")
        total_tokens = get_delta("openclaw_tokens_total")
        cost_usd = get_delta("openclaw_cost_usd_total", "openclaw_cost_usd")
        messages = get_delta("openclaw_message_processed_total", "openclaw_message_processed")

        if total_tokens == 0 and input_tokens == 0 and output_tokens == 0:
            return  # Nothing meaningful

        # otel-collector's Prometheus exporter replaces "." with "_" in attribute
        # keys: openclaw.model -> openclaw_model. Fall back to the dotted form
        # for exporters that don't do the rewrite, then to un-namespaced.
        model = labels.get("openclaw_model", labels.get("openclaw.model", labels.get("model", "unknown")))
        provider = labels.get("openclaw_provider", labels.get("openclaw.provider", labels.get("provider", "unknown")))

        event = {
            "tenant": Config.TENANT_NAME,
            "agent": Config.AGENT_NAME,
            "model": model,
            "provider": provider,
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "cache_read": int(cache_read),
            "cache_write": int(cache_write),
            "total_tokens": int(total_tokens) or int(input_tokens + output_tokens),
            "cost_usd": round(cost_usd, 6),
            "messages": int(messages),
            "timestamp": int(time.time() * 1000),
        }

        self.sqs_pusher.push_event(event)
        print(f"Pushed usage: in={int(input_tokens)} out={int(output_tokens)} "
              f"cache_r={int(cache_read)} cost=${cost_usd:.4f} msgs={int(messages)}")

    def run(self):
        """Main loop."""
        print(f"Starting metrics exporter for tenant={Config.TENANT_NAME}, agent={Config.AGENT_NAME}")
        print(f"Scraping otel-collector at {self.metrics_url}")
        print(f"Scrape interval: {Config.SCAN_INTERVAL_SECONDS}s")

        # Wait for otel-collector to be ready
        for attempt in range(10):
            raw = self.scrape()
            if raw is not None:
                print("otel-collector reachable")
                break
            print(f"Waiting for otel-collector... ({attempt + 1}/10)")
            time.sleep(5)
        else:
            print("WARNING: otel-collector not reachable after 50s, continuing anyway")

        while True:
            try:
                raw = self.scrape()
                if raw is not None:
                    self.compute_and_push(raw)
                self.sqs_pusher.flush()
            except Exception as e:
                print(f"Error in main loop: {e}")

            time.sleep(Config.SCAN_INTERVAL_SECONDS)


def main():
    try:
        Config.validate()
    except ValueError as e:
        print(f"Configuration error: {e}")
        return 1
    MetricsExporter().run()


if __name__ == "__main__":
    main()
