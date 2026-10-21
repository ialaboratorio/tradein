#!/bin/sh
set -e

mkdir -p /app/data 2>/dev/null || true

alembic upgrade head

exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
