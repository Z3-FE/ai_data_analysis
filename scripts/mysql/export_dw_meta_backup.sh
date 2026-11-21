#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker/docker-compose.yml"
BACKUP_FILE="${ROOT_DIR}/docker/mysql/backup/dw_meta.sql.gz"

mkdir -p "$(dirname "${BACKUP_FILE}")"

echo "==> Exporting MySQL databases: dw, meta"
docker compose -f "${COMPOSE_FILE}" exec -T mysql \
  mysqldump \
    -uroot -p123456 \
    --databases dw meta \
    --single-transaction \
    --routines \
    --triggers \
    --events \
    --hex-blob \
    --default-character-set=utf8mb4 \
  | gzip -c > "${BACKUP_FILE}"

echo "==> Backup written to ${BACKUP_FILE#${ROOT_DIR}/}"
