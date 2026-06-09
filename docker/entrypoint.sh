#!/usr/bin/env bash
# Container entrypoint: wait for Postgres, apply migrations, then serve.
#
# Runs from /app/backend (Dockerfile WORKDIR). Migrations apply on every start;
# alembic is idempotent (no-op at head). Safe for a single app instance — if you
# scale to multiple, move migrations to a one-shot init job to avoid races.
set -euo pipefail

PORT="${PORT:-8000}"

echo "==> Waiting for database…"
for i in $(seq 1 60); do
  if uv run python -c "
import asyncio
from app.config import load_db_settings
from sqlalchemy.ext.asyncio import create_async_engine
async def main():
    e = create_async_engine(load_db_settings().url)
    async with e.connect():
        pass
    await e.dispose()
asyncio.run(main())
" >/dev/null 2>&1; then
    echo "==> Database is up."
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "!! Database not reachable after 60s; exiting." >&2
    exit 1
  fi
  sleep 1
done

echo "==> Applying migrations (alembic upgrade head)…"
uv run alembic upgrade head

echo "==> Starting uvicorn on :${PORT}"
exec uv run uvicorn app.main:app --host 0.0.0.0 --port "${PORT}"
