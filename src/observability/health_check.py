import asyncio
import sys

import redis
from sqlalchemy import select

from src.core.config.settings import settings
from src.data.clients.postgres_client import engine
from src.infrastructure.celery.celery_app import celery_app


async def check_postgres() -> bool:
    """Verifies connection to PostgreSQL database."""
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
        return True
    except Exception as e:
        print(f"Health Check: Postgres connectivity failure: {e}", file=sys.stderr)
        return False


def check_redis() -> bool:
    """Verifies connection to Redis instance."""
    try:
        r = redis.Redis.from_url(settings.REDIS_URL)
        r.ping()
        return True
    except Exception as e:
        print(f"Health Check: Redis connectivity failure: {e}", file=sys.stderr)
        return False


def check_celery_ping() -> bool:
    """Verifies Celery worker is active and pingable."""
    try:
        inspect = celery_app.control.inspect()
        ping_res = inspect.ping()
        if ping_res:
            return True
        print("Health Check: Celery ping returned empty response.", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Health Check: Celery inspect ping failed: {e}", file=sys.stderr)
        return False


async def main() -> None:
    is_worker = len(sys.argv) > 1 and sys.argv[1] == "worker"

    postgres_ok = await check_postgres()
    redis_ok = check_redis()
    celery_ok = True

    if is_worker:
        celery_ok = check_celery_ping()

    if postgres_ok and redis_ok and celery_ok:
        print("Health check passed.")
        sys.exit(0)
    else:
        print("Health check failed.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
