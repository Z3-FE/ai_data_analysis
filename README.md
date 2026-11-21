# ai-data-analysis

这是一个面向 Olist 电商数据的自然语言数据分析项目。用户从前端提出问题，后端从 MySQL 的业务数据和元数据中找到相关信息，由 LLM 规划查询和分析步骤，再通过流式接口把过程和结果返回前端。

本文只介绍当前使用的搭建方式：**MySQL、Qdrant、Elasticsearch、Neo4j 使用 Docker；Embedding 使用外部兼容接口；PostgreSQL 在本机运行；FastAPI 和 Next.js 分别启动。** CSV 全量重建和本地 Embedding 服务不是默认启动链路。

## 一、服务分工

| 服务 | 用途 | 默认地址 | 启动方式 |
| --- | --- | --- | --- |
| MySQL | `dw` 业务数仓、`meta` 查询元数据 | `127.0.0.1:3309` | Docker Compose |
| PostgreSQL | 会话、消息、Harness 运行状态、记忆和 LangGraph Checkpointer | `127.0.0.1:5432`，数据库 `agent_app` | 本机 PostgreSQL |
| Qdrant | 元数据和记忆的向量检索 | `127.0.0.1:6333` | Docker Compose |
| Elasticsearch | 维度值全文检索 | `127.0.0.1:9200` | Docker Compose |
| Neo4j | Semantic Memory 的实体关系存储 | `bolt://127.0.0.1:7687` | Docker Compose |
| Embedding | 把文本转换成向量 | `https://api.siliconflow.cn/v1` | 外部接口 |
| LLM | 规划、生成 SQL、分析和回答 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 外部接口 |
| FastAPI | 后端 API 和 SSE 流式接口 | `http://127.0.0.1:8000` | 本机启动 |
| Next.js | 前端页面 | `http://127.0.0.1:3000` | 本机启动 |

## 二、环境要求

- Docker Desktop，并确认 Docker daemon 已启动。
- Python `3.14`、`uv`。
- Node.js、`pnpm`。
- 本机 PostgreSQL，能使用 `127.0.0.1:5432` 连接。
- 能访问外部 LLM 和 Embedding 服务的网络。

## 三、配置密钥

在项目根目录创建 `.env`（不要提交密钥）：

```dotenv
SILICONFLOW_API_KEY=你的硅基流动密钥
SILICONFLOW_BASE_URL=https://api.siliconflow.cn/v1
SILICONFLOW_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-4B

DASHSCOPE_API_KEY=你的百炼密钥
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_MODEL=qwen3.8-27b

POSTGRES_USER=你的本机PostgreSQL用户名
POSTGRES_PASSWORD=你的本机PostgreSQL密码

NEO4J_USER=neo4j
NEO4J_PASSWORD=password
NEO4J_DATABASE=neo4j
```

当前 Embedding 配置是 `provider: openai-compatible`，通过 SiliconFlow 外部接口调用 `Qwen/Qwen3-Embedding-4B`，向量维度为 `1024`。默认流程**不使用** `docker/embedding/bge-large-zh-v1.5`，也不需要启动 Compose 中的 `embedding` 服务；那部分只是保留的本地推理备用配置。

## 四、启动 Docker 基础服务

在项目根目录执行：

```bash
docker compose -f docker/docker-compose.yml up -d mysql qdrant elasticsearch neo4j
docker compose -f docker/docker-compose.yml ps
```

等待 MySQL、Elasticsearch、Neo4j 变为 healthy。Qdrant 可以用下面的地址检查：

```bash
curl http://127.0.0.1:6333/collections
curl http://127.0.0.1:9200/_cluster/health
```

## 五、准备 MySQL 数据

### 5.1 默认方式：恢复项目内备份

当前开发数据库实际存放在 Docker volume `ai_data_analysis_mysql_data` 的 `/var/lib/mysql` 中。项目目录 `docker/mysql/data/` 不是数据库文件，只是 CSV 原始文件的只读挂载目录。

已从当前运行中的 `ai_data_analysis_mysql` 容器导出 `dw` 和 `meta`，备份文件在：

```text
docker/mysql/backup/dw_meta.sql.gz
```

在项目根目录执行：

```bash
bash scripts/mysql/restore_dw_meta_backup.sh
```

恢复脚本会启动 MySQL、等待健康检查通过，再把备份导入容器。备份包含完整的 `dw` 和 `meta` 建表语句及数据，因此不需要准备缺失的 `olist_geolocation_dataset.csv`。

如果要搭建全新的本地数据库，确认不需要保留当前 Docker 数据后再执行；`down -v` 会删除本项目 Docker volume：

```bash
docker compose -f docker/docker-compose.yml down -v
bash scripts/mysql/restore_dw_meta_backup.sh
```

### 5.2 从当前 MySQL 重新导出备份

导出来源不是 CSV，而是当前正在运行的 MySQL 容器。执行：

```bash
bash scripts/mysql/export_dw_meta_backup.sh
```

脚本实际调用容器内的 `mysqldump`，只导出 `dw` 和 `meta`，覆盖写入 `docker/mysql/backup/dw_meta.sql.gz`。这个操作不会导出 PostgreSQL、Qdrant、Elasticsearch 或 Neo4j。

### 5.3 CSV 入口的边界

项目仍保留 `scripts/import_olist_to_mysql.sh` 和 `scripts/mysql/` 下的建模 SQL，用于以后修改数仓建模逻辑时重建 MySQL。它不是默认搭建方式，而且当前 `docker/mysql/data/raw/olist/` 缺少 `olist_geolocation_dataset.csv`，不能保证从 CSV 直接完整重建出当前备份内容。

## 六、重建检索索引

MySQL 备份不包含 Qdrant 和 Elasticsearch 的索引。恢复 MySQL 后，在项目根目录执行：

```bash
uv run python -m app.scripts.build_meta_vectors
uv run python -m app.scripts.build_dimension_value_indexes
```

两个脚本都使用 `.env` 中的 SiliconFlow 外部 Embedding 接口：

- `build_meta_vectors` 读取 `meta.tables`、`meta.columns`、`meta.metrics`，重建 Qdrant 的表、字段和指标向量集合。
- `build_dimension_value_indexes` 读取 `meta.dimension_values`，同时重建 Elasticsearch 全文索引和 Qdrant 维度值向量集合。

更换 Embedding 模型或维度后，必须重新构建所有相关索引，不能混用不同模型生成的向量。

## 七、准备 PostgreSQL

PostgreSQL 不在当前 `docker/docker-compose.yml` 中。请先在本机安装并启动 PostgreSQL，然后创建与 `config.yaml` 一致的数据库：

```bash
createdb agent_app
```

如果当前账号没有权限，就用 PostgreSQL 管理工具创建 `agent_app`，并把 `.env` 中的 `POSTGRES_USER`、`POSTGRES_PASSWORD` 改成实际账号。

后端启动时执行 `Base.metadata.create_all(...)`，自动创建会话、消息、Harness、记忆和上下文相关业务表。`scripts/postgres/` 中的 SQL 是历史或手工初始化脚本，不需要在默认流程中逐个执行。

## 八、启动 FastAPI 后端

```bash
uv sync
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

启动检查：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/ping
```

常用接口：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/health` | 检查后端是否存活 |
| `GET` | `/api/ping` | 检查 API 路由 |
| `GET` | `/api/meta/tables` | 查询 MySQL `meta.tables` |
| `POST` | `/api/harness/run/stream` | 启动 Harness 流式运行 |
| `POST` | `/api/harness/run/resume/stream` | 恢复等待确认的 Harness 运行 |

## 九、启动前端

打开另一个终端：

```bash
cd frontend
pnpm install
pnpm dev
```

浏览器访问 `http://127.0.0.1:3000`。`frontend/next.config.ts` 默认把 `/api/*` 代理到 `http://127.0.0.1:8000`。后端地址改变时：

```bash
FASTAPI_BASE_URL=http://127.0.0.1:8000 pnpm dev
```

## 十、完整启动顺序

1. 安装 Docker、Python/uv、Node.js/pnpm 和本机 PostgreSQL。
2. 创建 `.env`，填写 SiliconFlow Embedding、DashScope LLM 和 PostgreSQL 配置。
3. 执行 `docker compose -f docker/docker-compose.yml up -d mysql qdrant elasticsearch neo4j`。
4. 执行 `bash scripts/mysql/restore_dw_meta_backup.sh`，恢复 MySQL 的 `dw` 和 `meta`。
5. 执行 `uv sync`，再运行两个索引构建脚本，使用外部 Embedding 生成 Qdrant/Elasticsearch 索引。
6. 创建本机 PostgreSQL 数据库 `agent_app`。
7. 启动 FastAPI：`uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`。
8. 进入 `frontend`，执行 `pnpm install && pnpm dev`。
9. 浏览器打开 `http://127.0.0.1:3000`。

## 十一、停止服务

停止 Docker 服务但保留数据：

```bash
docker compose -f docker/docker-compose.yml down
```

停止并删除 Docker volume 中的数据：

```bash
docker compose -f docker/docker-compose.yml down -v
```

第二条命令会删除本项目 Docker 管理的 MySQL、Qdrant、Elasticsearch 和 Neo4j 数据，执行前确认不需要保留本机数据。
