from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, generate_latest

REQUEST_COUNT = Counter("api_requests_total", "Total API requests", ["endpoint", "method"])
SIGNALS_CREATED = Counter("signals_created_total", "Signals generated", ["strategy", "symbol"])
ORDERS_PLACED = Counter("orders_placed_total", "Orders placed", ["mode", "type"])
EQUITY_GAUGE = Gauge("equity_current", "Current equity", ["mode"])
TASK_RUNTIME = Histogram("worker_task_runtime_seconds", "Worker task runtime", ["task"])


def metrics_payload() -> bytes:
    return generate_latest()
