from __future__ import annotations

import os
import sys
from pathlib import Path

# Ensure `backend/` is on sys.path when script is launched as `python scripts/run_worker.py`.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.workers.celery_app import celery_app


def main() -> None:
    concurrency = os.getenv("CELERY_CONCURRENCY", "1")
    queues = os.getenv("CELERY_QUEUES", "").strip()
    with_beat = os.getenv("CELERY_WITH_BEAT", "1").strip().lower() not in {"0", "false", "no", "off"}
    pool = os.getenv("CELERY_POOL", "").strip()
    max_tasks_per_child = os.getenv("CELERY_MAX_TASKS_PER_CHILD", "").strip()
    prefetch_multiplier = os.getenv("CELERY_PREFETCH_MULTIPLIER", "").strip()
    without_gossip = os.getenv("CELERY_WITHOUT_GOSSIP", "1").strip().lower() not in {"0", "false", "no", "off"}
    without_mingle = os.getenv("CELERY_WITHOUT_MINGLE", "1").strip().lower() not in {"0", "false", "no", "off"}

    args = [
        "worker",
        "--loglevel=info",
        f"--concurrency={concurrency}",
    ]
    if pool:
        args.append(f"--pool={pool}")
    if max_tasks_per_child:
        args.append(f"--max-tasks-per-child={max_tasks_per_child}")
    if prefetch_multiplier:
        args.append(f"--prefetch-multiplier={prefetch_multiplier}")
    if queues:
        args.append(f"--queues={queues}")
    if without_gossip:
        args.append("--without-gossip")
    if without_mingle:
        args.append("--without-mingle")
    if with_beat:
        args.append("--beat")

    celery_app.worker_main(args)


if __name__ == "__main__":
    main()
