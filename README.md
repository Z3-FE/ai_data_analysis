# ai-data-analysis

## MySQL with Docker

This project includes a Docker Compose setup for a local MySQL database.

1. Start MySQL:

   ```bash
   docker compose -f docker/docker-compose.yml up -d mysql
   ```

2. Check the container status:

   ```bash
   docker compose -f docker/docker-compose.yml ps
   ```

3. Connect from your local machine:

   ```bash
   mysql -h 127.0.0.1 -P 3309 -u app_user -p dw
   ```

The password is `123456`.

Put SQL files in `docker/mysql/init/` before the first startup if you want MySQL
to run initialization scripts automatically. These scripts only run when the
Docker volume is created for the first time.

To stop the database:

```bash
docker compose -f docker/docker-compose.yml down
```

To delete the database data volume and start fresh:

```bash
docker compose -f docker/docker-compose.yml down -v
```

## Import Olist Data

The project imports CSV files from `docker/mysql/data/raw/olist/` into two
MySQL databases:

- `dw`: dimensional warehouse tables, including `dim_*` and `fact_*`.
- `meta`: semantic metadata for metrics, dimensions, table relationships, and
  query examples.

Run the Docker-based import:

```bash
bash scripts/import_olist_to_mysql.sh
```

The script starts the MySQL container, mounts CSV files into the container at
`/import-data`, recreates `dw` and `meta`, loads CSV data into temporary staging
tables, builds dimensional tables, drops staging tables, and prints validation
row counts.

SQL files are executed in this order:

| File | Purpose |
| --- | --- |
| `scripts/mysql/01_reset_databases.sql` | Recreates `dw` and `meta`, removes the old experiment database, and grants `app_user` permissions. |
| `scripts/mysql/02_create_stage_tables.sql` | Creates temporary `stg_*` tables that match the source CSV files. |
| `scripts/mysql/03_load_stage_csv.sql` | Loads CSV files from `/import-data` into `stg_*` tables and adds temporary join indexes. |
| `scripts/mysql/04_build_dw_tables.sql` | Builds the final `dim_*` and `fact_*` warehouse tables, then drops the staging tables. |
| `scripts/mysql/05_build_meta_tables.sql` | Builds AI semantic metadata: tables, columns, relationships, metrics, dimensions, subject areas, terms, and examples. |
| `scripts/mysql/07_update_aliases.sql` | Adds aliases for metadata tables and columns to improve semantic retrieval. |
| `scripts/mysql/08_update_metric_aliases.sql` | Adds metric aliases and common business names to improve semantic retrieval. |
| `scripts/mysql/06_validate_import.sql` | Prints row counts and key validation metrics after import. |
## FastAPI Backend

The backend is currently a minimal FastAPI skeleton with a first MySQL-backed API call.

- `GET /health` returns `{"status": "ok"}`
- `GET /api/ping` returns `{"message": "pong"}`
- `GET /api/meta/tables` returns table metadata from the `meta.tables` table

Run the server:

```bash
uv run uvicorn app.main:app --reload
```

Current local service defaults are defined in `config.yaml`:

- MySQL host: `127.0.0.1`
- MySQL port: `3309`
- MySQL user: `app_user`
- MySQL password: `123456`
- DW database: `dw`
- Meta database: `meta`
- Qdrant URL: `http://127.0.0.1:6333`
- Embedding URL: `http://127.0.0.1:8081`
- Table vector collection: `meta_tables_semantic`
- Column vector collection: `meta_columns_semantic`
- Metric vector collection: `meta_metrics_semantic`

Logging defaults are also defined in `config.yaml`:

- Log level: `INFO`
- Log format: `时间 | 级别 | 模块名 | 日志内容`

需要调整日志级别时，修改 `config.yaml`：

```yaml
logging:
  level: DEBUG
```

## Build Metadata Vectors

统一构建三类元数据向量：

| Collection | 向量来源 | 预期 points |
| --- | --- | ---: |
| `meta_tables_semantic` | `business_name`、`table_name`、`description`、`grain`、`aliases` | 60 |
| `meta_columns_semantic` | `column_name`、`business_name`、`description`、`aliases` | 56 |
| `meta_metrics_semantic` | `metric_name`、`business_name`、`description`、`aliases` | 40 |

指标计算口径 `expression_sql`、基础表 `base_table_id`、聚合方式和单位只保留在
payload 中，不单独生成向量。

先启动依赖服务：

```bash
docker compose -f docker/docker-compose.yml up -d mysql qdrant embedding
```

执行统一构建：

```bash
uv run python -m app.scripts.build_meta_vectors
```

脚本按 tables、columns、metrics 的顺序重建 collection，成功时总计应为 156 个
points。当前 macOS Docker 环境使用 TEI CPU 后端，批次大小设为 1，以规避
cpu-1.8 内部队列阻塞。批次大小和重试参数统一配置在 `config.yaml` 中。
