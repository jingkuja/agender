from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server

TASK_DURATION = Histogram(
    "agender_task_duration_seconds",
    "Task duration",
    ["type", "status"],
)
TASKS_TOTAL = Counter("agender_tasks_total", "Tasks finished", ["type", "status"])
EXTRACT_SUCCESS = Counter("agender_extract_success_total", "Successful extracts", ["type"])
BLOCKED_TOTAL = Counter("agender_blocked_total", "Blocked tasks", ["site", "code"])
CLAIM_EMPTY = Counter("agender_claim_empty_total", "Empty claims")
BROWSER_UP = Gauge("agender_browser_up", "Browser process up")
LAST_SUCCESS = Gauge("agender_last_success_unixtime", "Last success unix time", ["type"])


def start_metrics(addr: str) -> None:
    host, port_s = addr.rsplit(":", 1)
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("metrics must bind loopback")
    start_http_server(int(port_s), addr=host)
