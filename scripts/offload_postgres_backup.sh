#!/usr/bin/env sh
set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
OFFLOAD_TARGET="${BACKUP_OFFLOAD_TARGET:-s3://mlflow-db-backups/}"
MINIO_ENDPOINT="${MLFLOW_S3_ENDPOINT_URL:-http://minio:9000}"
MINIO_USER="${AWS_ACCESS_KEY_ID:-}"
MINIO_PASSWORD="${AWS_SECRET_ACCESS_KEY:-}"
RETENTION_DAYS="${BACKUP_OFFLOAD_RETENTION_DAYS:-30}"
STATE_FILE="${BACKUP_OFFLOAD_STATE_FILE:-/tmp/.last_offloaded}"

if [ -z "$MINIO_USER" ] || [ -z "$MINIO_PASSWORD" ]; then
  echo "offload: missing MinIO credentials" >&2
  exit 1
fi

if [ -L "${BACKUP_DIR}/daily/mlflow-latest.sql.gz" ]; then
  latest="$(readlink -f "${BACKUP_DIR}/daily/mlflow-latest.sql.gz" 2>/dev/null || realpath "${BACKUP_DIR}/daily/mlflow-latest.sql.gz" 2>/dev/null || echo "${BACKUP_DIR}/daily/mlflow-latest.sql.gz")"
elif [ -f "${BACKUP_DIR}/daily/mlflow-latest.sql.gz" ]; then
  latest="${BACKUP_DIR}/daily/mlflow-latest.sql.gz"
else
  latest="$(find "$BACKUP_DIR" -type f \( -name '*.sql.gz' -o -name '*.sql' -o -name '*.dump' \) 2>/dev/null | while read -r file; do
    if stat -c %Y "$file" >/dev/null 2>&1; then
      echo "$(stat -c %Y "$file") $file"
    else
      echo "$(stat -f %m "$file") $file"
    fi
  done | sort -nr | head -n 1 | cut -d' ' -f2- || true)"
fi

if [ -z "$latest" ] || [ ! -f "$latest" ]; then
  echo "offload: no backup files found in $BACKUP_DIR"
  exit 0
fi

if [ -f "$STATE_FILE" ] && [ "$(cat "$STATE_FILE")" = "$latest" ]; then
  echo "offload: already uploaded $latest"
  exit 0
fi

mc alias set offload "$MINIO_ENDPOINT" "$MINIO_USER" "$MINIO_PASSWORD" >/dev/null
bucket="${OFFLOAD_TARGET#s3://}"
bucket="${bucket%%/*}"
prefix="${OFFLOAD_TARGET#s3://$bucket}"
prefix="${prefix#/}"
remote_path="offload/${bucket}/${prefix:+${prefix}/}$(basename "$latest")"

mc mb --ignore-existing "offload/${bucket}" >/dev/null
mc cp "$latest" "$remote_path"
echo "$latest" > "$STATE_FILE"
echo "offload: uploaded $latest -> $remote_path"

if [ "$RETENTION_DAYS" -gt 0 ] 2>/dev/null; then
  find "$BACKUP_DIR" -type f \( -name '*.sql.gz' -o -name '*.sql' -o -name '*.dump' \) -mtime "+${RETENTION_DAYS}" -delete || true
fi
