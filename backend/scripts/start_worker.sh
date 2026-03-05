#!/usr/bin/env bash
set -euo pipefail

until alembic upgrade head; do
  sleep 5
done

exec python -m celery -A app.workers.celery_app:celery_app worker --beat --loglevel=info --concurrency="${CELERY_CONCURRENCY:-2}"

