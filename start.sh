#!/usr/bin/env sh
set -eu
# Render Free uses an ephemeral filesystem. Rebuild user data from the bundled
# seed at every process start; runtime history is disposable.
DB_PATH="${SMARTNANO_DB:-/tmp/smartnano.db}"
DB_DIR=$(dirname "$DB_PATH")
SEED_DB="${SMARTNANO_SEED_DB:-/app/smartnano.db}"
mkdir -p "$DB_DIR"
if [ ! -f "$SEED_DB" ]; then
  echo "Seed database not found: $SEED_DB" >&2
  exit 1
fi
rm -f "$DB_PATH-wal" "$DB_PATH-shm"
cp "$SEED_DB" "$DB_PATH"
export SMARTNANO_DB="$DB_PATH"
export SMARTNANO_HOST="0.0.0.0"
exec python /app/server.py "${PORT:-10000}"
