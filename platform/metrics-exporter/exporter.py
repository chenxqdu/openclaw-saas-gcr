"""Metrics exporter: scrapes otel-collector Prometheus endpoint, computes deltas, pushes to SQS."""
import time
import urllib.request
from typing import Dict, Optional
from config import Config
from sqs_pusher import SQSPusher


# Metrics we care about for billing (all are counters → monotonically increasing)
BILLING_METRICS = {
    "openclaw_tokens_input_total",
    "openclaw_tokens_output_total",
    "openclaw_tokens_cache_read_total",
    "openclaw_tokens_cache_write_total",
    "openclaw_tokens_total_total",
    "openclaw_cost_usd_total",
    "openclaw_message_processed_total",
    # Also try without _total suffix (depends on otel-collector version)
    "openclaw_tokens_input",
    "openclaw_tokens_output",
    "openclaw_tokens_cache_read",
    "openclaw_tokens_cache_write",
    "openclaw_tokens_total",
    "openclaw_cost_usd",
    "openclaw_message_processed",
}


def parse_prometheus_text(text: str) -> Dict[str, float]:
    """Parse Prometheus text exposition format into {metric_name: value}.

    For simplicity, we sum across all label combinations per metric name.
    This works because each agent pod has exactly one tenant+agent.
    """
    metrics: Dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Format: metric_name{labels} value [timestamp]
        # or:     metric_name value [timestamp]
        try:
            parts = line.split()
            if len(parts) < 2:
                continue
            name_with_labels = parts[0]
            value = float(parts[1])

            # Extract base metric name (strip {labels})
            name = name_with_labels.split("{")[0]

            if name in BILLING_METRICS:
                # Sum across label combinations
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

        input_tokens = get_delta("openclaw_tokens_input_total", "openclaw_tokens_input")
        output_tokens = get_delta("openclaw_tokens_output_total", "openclaw_tokens_output")
        cache_read = get_delta("openclaw_tokens_cache_read_total", "openclaw_tokens_cache_read")
        cache_write = get_delta("openclaw_tokens_cache_write_total", "openclaw_tokens_cache_write")
        total_tokens = get_delta("openclaw_tokens_total_total", "openclaw_tokens_total")
        cost_usd = get_delta("openclaw_cost_usd_total", "openclaw_cost_usd")
        messages = get_delta("openclaw_message_processed_total", "openclaw_message_processed")

        if total_tokens == 0 and input_tokens == 0 and output_tokens == 0:
            return  # Nothing meaningful

        model = labels.get("openclaw.model", labels.get("model", "unknown"))
        provider = labels.get("openclaw.provider", labels.get("provider", "unknown"))

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
