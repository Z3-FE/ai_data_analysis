#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker/docker-compose.yml"
BACKUP_FILE="${ROOT_DIR}/docker/mysql/backup/dw_meta.sql.gz"

if [[ ! -f "${BACKUP_FILE}" ]]; then
  echo "Backup file not found: ${BACKUP_FILE}" >&2
  exit 1
fi

echo "==> Starting MySQL"
docker compose -f "${COMPOSE_FILE}" up -d mysql

echo "==> Waiting for MySQL healthcheck"
for _ in $(seq 1 60); do
  health_state="$(
    docker inspect -f '{{.State.Health.Status}}' ai_data_analysis_mysql 2>/dev/null || true
  )"
  if [[ "${health_state}" == "healthy" ]]; then
    break
  fi
  sleep 2
done

if [[ "${health_state:-}" != "healthy" ]]; then
  docker logs --tail=120 ai_data_analysis_mysql
  echo "MySQL did not become healthy in time." >&2
  exit 1
fi

echo "==> Restoring ${BACKUP_FILE#${ROOT_DIR}/}"
gzip -dc "${BACKUP_FILE}" \
  | docker compose -f "${COMPOSE_FILE}" exec -T mysql \
      mysql -uroot -p123456 --default-character-set=utf8mb4

echo "==> MySQL dw/meta restore finished"
