#!/bin/sh
set -e

PORT="${PORT:-8080}"
CELERY_MODE="${1:-worker}"

if [ "$CELERY_MODE" = "beat" ]; then
  /app/.venv/bin/celery -A src.infrastructure.celery.celery_app beat --loglevel=info &
else
  /app/.venv/bin/celery -A src.infrastructure.celery.celery_app worker --loglevel=info &
fi

CELERY_PID=$!
trap 'kill "$CELERY_PID" 2>/dev/null || true' EXIT TERM INT

exec /app/.venv/bin/python -m src.observability.cloud_run_health --port "$PORT"
