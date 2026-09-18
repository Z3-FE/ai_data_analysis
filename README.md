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

The backend is a FastAPI + LangGraph Agent harness: conversations with SSE
streaming, semantic retrieval over metadata vectors, and agent memory. The
routes live in `app/api/routers/` (`meta`, `agent`, `conversations`, `harness`).

- `GET /health` returns `{"status": "ok"}`
- `GET /api/ping` returns `{"message": "pong"}`
- `GET /api/meta/tables` returns table metadata from the `meta.tables` table
- `POST /api/agent/run/stream` runs a streaming agent analysis
- `POST /api/harness/run/resume/stream` resumes a paused run after confirmation

Run the server:

```bash
uv run uvicorn app.main:app --reload
```

Dependency services: MySQL、Qdrant、Elasticsearch、Neo4j run in Docker, and
Postgres runs as a local service (conversations and the LangGraph checkpointer):

```bash
docker compose -f docker/docker-compose.yml up -d mysql qdrant elasticsearch neo4j
```

Current local service defaults are defined in `config.yaml`:

- MySQL host: `127.0.0.1`
- MySQL port: `3309`
- MySQL user: `app_user`
- MySQL password: `123456`
- DW database: `dw`
- Meta database: `meta`
- Postgres: `127.0.0.1:5432`, database `agent_app`
- Neo4j: `bolt://127.0.0.1:7687`（Semantic Memory 实体关系投影）
- Elasticsearch: `http://127.0.0.1:9200`（维度值全文检索）
- Qdrant URL: `http://127.0.0.1:6333`
- Embedding: 硅基流动 OpenAI 兼容接口，模型 `Qwen/Qwen3-Embedding-4B`（1024 维），
  API Key 从 `.env` 的 `SILICONFLOW_API_KEY` 读取；仅 `provider: local` 时才使用
  `http://127.0.0.1:8081`
- Table vector collection: `meta_tables_semantic`
- Column vector collection: `meta_columns_semantic`
- Metric vector collection: `meta_metrics_semantic`
- Dimension value vector collection: `meta_dimension_values_semantic`

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
| `meta_columns_semantic` | `column_name`、`business_name`、`description`、`aliases` | 188 |
| `meta_metrics_semantic` | `metric_name`、`business_name`、`description`、`aliases` | 40 |

指标计算口径 `expression_sql`、基础表 `base_table_id`、聚合方式和单位只保留在
payload 中，不单独生成向量。

先启动依赖服务：

```bash
docker compose -f docker/docker-compose.yml up -d mysql qdrant
```

执行统一构建：

```bash
uv run python -m app.scripts.build_meta_vectors
```

脚本按 tables、columns、metrics 的顺序重建 collection，成功时总计应为 288 个
points。Embedding 当前使用硅基流动云端接口（`Qwen/Qwen3-Embedding-4B`，1024 维），
需在 `.env` 中配置 `SILICONFLOW_API_KEY`；批次大小等参数统一配置在 `config.yaml`
中。切换 embedding 模型意味着向量空间整体变化，必须重建所有向量集合。

## Build Dimension Value Indexes

维度值同时构建 Elasticsearch 全文索引和 Qdrant 语义索引（当前共 655 个 points）：

```bash
uv run python -m app.scripts.build_dimension_value_indexes
```

脚本重建 ES 物理索引并原子切换 alias；Qdrant 侧按 `point_id` 断点续建。注意：
更换 embedding 模型后重跑前需先删除 `meta_dimension_values_semantic` collection，
否则已存在的旧向量会被跳过，导致新旧向量空间混用。
