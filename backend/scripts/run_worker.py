from __future__ import annotations

import os

from app.workers.celery_app import celery_app


def main() -> None:
    concurrency = os.getenv("CELERY_CONCURRENCY", "2")
    celery_app.worker_main(
        [
            "worker",
            "--beat",
            "--loglevel=info",
            f"--concurrency={concurrency}",
        ]
    )


if __name__ == "__main__":
    main()

