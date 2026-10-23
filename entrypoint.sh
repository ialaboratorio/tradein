#!/bin/sh
set -e

mkdir -p /app/data 2>/dev/null || true
DB_PATH="${LITESTREAM_DB_PATH:-/app/data/tradein.db}"
PORT="${PORT:-8000}"

if [ -n "${LITESTREAM_REPLICA_URL:-}" ]; then
    litestream restore -if-db-not-exists -if-replica-exists -o "$DB_PATH" "$LITESTREAM_REPLICA_URL"

    if [ ! -f "$DB_PATH" ] && [ -n "${SEED_BUCKET:-}" ]; then
        SEED_BUCKET="$SEED_BUCKET" DB_PATH="$DB_PATH" python - <<'PY'
import json
import os
import urllib.parse
import urllib.request

db_path = os.environ["DB_PATH"]
bucket = os.environ["SEED_BUCKET"]
obj = os.environ.get("SEED_OBJECT", "seed/tradein.db")
meta = urllib.request.Request(
    "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
    headers={"Metadata-Flavor": "Google"},
)
token = json.load(urllib.request.urlopen(meta, timeout=5))["access_token"]
url = (
    "https://storage.googleapis.com/storage/v1/b/"
    f"{bucket}/o/{urllib.parse.quote(obj, safe='')}?alt=media"
)
req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
tmp = db_path + ".seed"
with urllib.request.urlopen(req, timeout=600) as src, open(tmp, "wb") as dst:
    while chunk := src.read(1024 * 1024):
        dst.write(chunk)
os.replace(tmp, db_path)
PY
    fi

    alembic upgrade head
    exec litestream replicate -exec "uvicorn app.main:app --host 0.0.0.0 --port $PORT" "$DB_PATH" "$LITESTREAM_REPLICA_URL"
fi

alembic upgrade head
exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT"
