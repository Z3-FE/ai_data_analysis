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
   mysql -h 127.0.0.1 -P 3309 -u app_user -p ai_data_analysis
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
| `scripts/mysql/06_validate_import.sql` | Prints row counts and key validation metrics after import. |
## FastAPI Backend

The backend is currently a minimal FastAPI skeleton with two routes:

- `GET /health` returns `{"status": "ok"}`
- `GET /api/ping` returns `{"message": "pong"}`

Run the server:

```bash
uv run uvicorn app.main:app --reload
```
