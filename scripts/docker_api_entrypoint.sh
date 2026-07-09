#!/bin/sh
set -eu

echo "[api] Applying database migrations..."
python -m alembic upgrade head

echo "[api] Seeding default symbols, sources and strategies..."
python -m ict.cli db seed-defaults

if [ "${MARKET_ARCHIVE_STARTUP_RESTORE_ENABLED:-true}" = "true" ]; then
  if [ -n "${MARKET_ARCHIVE_KEY:-}" ] \
    && [ -n "${R2_ACCESS_KEY_ID:-}" ] \
    && [ -n "${R2_SECRET_ACCESS_KEY:-}" ] \
    && [ -n "${R2_BUCKET:-}" ] \
    && { [ -n "${R2_ACCOUNT_ID:-}" ] || [ -n "${R2_ENDPOINT_URL:-}" ]; }; then
    echo "[api] Restoring recent R2 archive partitions..."
    RESTORE_ARGS="archive restore-from-r2 --days ${MARKET_ARCHIVE_STARTUP_DAYS:-7} --continue-on-missing --skip-existing-local --max-download-mb ${MARKET_ARCHIVE_STARTUP_MAX_DOWNLOAD_MB:-1024}"
    if [ -n "${MARKET_ARCHIVE_STARTUP_SYMBOLS:-}" ]; then
      RESTORE_ARGS="$RESTORE_ARGS --symbols ${MARKET_ARCHIVE_STARTUP_SYMBOLS}"
    fi
    if [ -n "${MARKET_ARCHIVE_STARTUP_SOURCES:-}" ]; then
      RESTORE_ARGS="$RESTORE_ARGS --sources ${MARKET_ARCHIVE_STARTUP_SOURCES}"
    fi
    if python -m ict.cli $RESTORE_ARGS; then
      echo "[api] R2 startup restore completed."
    elif [ "${MARKET_ARCHIVE_STARTUP_RESTORE_FAIL_FAST:-false}" = "true" ]; then
      echo "[api] R2 startup restore failed and fail-fast is enabled."
      exit 1
    else
      echo "[api] R2 startup restore failed; continuing API startup."
    fi
  else
    echo "[api] R2 startup restore skipped: archive secrets are not fully configured."
  fi
else
  echo "[api] R2 startup restore disabled."
fi

echo "[api] Starting FastAPI..."
exec python -m uvicorn ict.api.app:app --host 0.0.0.0 --port 8000 --reload
