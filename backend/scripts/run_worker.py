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
    concurrency = os.getenv("CELERY_CONCURRENCY", "2")
    queues = os.getenv("CELERY_QUEUES", "").strip()
    with_beat = os.getenv("CELERY_WITH_BEAT", "1").strip().lower() not in {"0", "false", "no", "off"}

    args = [
        "worker",
        "--loglevel=info",
        f"--concurrency={concurrency}",
    ]
    if queues:
        args.append(f"--queues={queues}")
    if with_beat:
        args.append("--beat")

    celery_app.worker_main(args)


if __name__ == "__main__":
    main()
