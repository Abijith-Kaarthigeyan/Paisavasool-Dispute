#!/bin/sh
set -e

PORT="${PORT:-8002}"
RUN_MIGRATIONS="${RUN_MIGRATIONS:-true}"

if [ "$RUN_MIGRATIONS" = "true" ]; then
  /app/.venv/bin/alembic upgrade head
fi

exec /app/.venv/bin/uvicorn main:app --host 0.0.0.0 --port "$PORT"
