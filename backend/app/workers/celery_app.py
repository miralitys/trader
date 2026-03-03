from __future__ import annotations

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "trader_worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    imports=("app.workers.tasks",),
)

celery_app.conf.beat_schedule = {
    "ingest-market-data-every-2-min": {
        "task": "app.workers.tasks.ingest_market_data_task",
        "schedule": 120.0,
    },
    "run-strategies-every-5-min": {
        "task": "app.workers.tasks.strategy_runner_task",
        "schedule": 300.0,
    },
    "paper-execution-every-1-min": {
        "task": "app.workers.tasks.paper_execution_task",
        "schedule": 60.0,
    },
    "live-execution-every-1-min": {
        "task": "app.workers.tasks.live_execution_task",
        "schedule": 60.0,
    },
    "reconciliation-every-3-min": {
        "task": "app.workers.tasks.reconciliation_task",
        "schedule": 180.0,
    },
    "weekly-universe-refresh": {
        "task": "app.workers.tasks.universe_selector_task",
        "schedule": 604800.0,
    },
}

# Ensure all shared_task declarations are registered in this Celery app.
import app.workers.tasks  # noqa: E402,F401
