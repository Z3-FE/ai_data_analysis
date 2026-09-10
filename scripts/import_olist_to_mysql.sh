#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${ROOT_DIR}/docker/docker-compose.yml"
MYSQL_SERVICE="mysql"
MYSQL_USER="root"
MYSQL_PASSWORD="123456"

run_mysql() {
  local sql_file="$1"
  echo "==> Running ${sql_file#${ROOT_DIR}/}"
  docker compose -f "${COMPOSE_FILE}" exec -T "${MYSQL_SERVICE}" \
    mysql --local-infile=1 --default-character-set=utf8mb4 \
      -u"${MYSQL_USER}" -p"${MYSQL_PASSWORD}" < "${sql_file}"
}

echo "==> Starting MySQL container"
docker compose -f "${COMPOSE_FILE}" up -d --force-recreate "${MYSQL_SERVICE}"

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

run_mysql "${ROOT_DIR}/scripts/mysql/01_reset_databases.sql"
run_mysql "${ROOT_DIR}/scripts/mysql/02_create_stage_tables.sql"
run_mysql "${ROOT_DIR}/scripts/mysql/03_load_stage_csv.sql"
run_mysql "${ROOT_DIR}/scripts/mysql/04_build_dw_tables.sql"
run_mysql "${ROOT_DIR}/scripts/mysql/05_build_meta_tables.sql"
run_mysql "${ROOT_DIR}/scripts/mysql/07_update_aliases.sql"
run_mysql "${ROOT_DIR}/scripts/mysql/08_update_metric_aliases.sql"
run_mysql "${ROOT_DIR}/scripts/mysql/09_create_dimension_values.sql"
run_mysql "${ROOT_DIR}/scripts/mysql/10_update_dimension_value_aliases.sql"
run_mysql "${ROOT_DIR}/scripts/mysql/12_update_meta_metric_grain_rules.sql"
run_mysql "${ROOT_DIR}/scripts/mysql/06_validate_import.sql"

echo "==> Import finished"
