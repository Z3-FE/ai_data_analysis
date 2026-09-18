"""读取项目统一 YAML 配置。

配置集中放在项目根目录的 `config.yaml`，业务代码通过 `settings` 使用配置，
避免在不同模块里散落服务地址、端口、集合名等硬编码。
"""

import os
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any

from omegaconf import OmegaConf

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


@dataclass(frozen=True)
class AppConfig:
    """应用基础信息配置。"""

    name: str
    version: str
    default_user_id: str


@dataclass(frozen=True)
class HarnessConfig:
    """Data Agent Harness 的运行边界配置。"""

    # Planner 单个动作规划阶段允许的最大重试次数。
    max_planner_retries: int
    # 具备幂等条件的工具调用允许的最大重试次数。
    max_tool_retries: int
    # 单次 Harness 运行允许完成的最大工具迭代次数。
    max_iterations: int
    # 从 start 到终态或暂停的最大运行时长，单位为秒。
    run_timeout_seconds: int
    # query_data 单次工具调用的超时时间，单位为秒。
    query_timeout_seconds: int
    # analyze_data 单次工具调用的超时时间，单位为秒。
    analyze_timeout_seconds: int
    # build_report 单次工具调用的超时时间，单位为秒。
    report_timeout_seconds: int
    # SSE 在没有业务事件时发送注释心跳的间隔，单位为秒。
    sse_heartbeat_seconds: int


@dataclass(frozen=True)
class MysqlConfig:
    """MySQL 连接配置。"""

    host: str
    port: int
    user: str
    password: str
    dw_database: str
    meta_database: str
    charset: str
    pool_pre_ping: bool
    pool_recycle: int

    def database_url(self, database: str) -> str:
        """根据数据库名拼接 SQLAlchemy MySQL 连接 URL。"""
        return (
            f"mysql+pymysql://{self.user}:{self.password}"
            f"@{self.host}:{self.port}/{database}?charset={self.charset}"
        )

    @cached_property
    def dw_database_url(self) -> str:
        """返回连接 DW 数据库的 SQLAlchemy URL。"""
        return self.database_url(self.dw_database)

    @cached_property
    def meta_database_url(self) -> str:
        """返回连接 meta 元数据库的 SQLAlchemy URL。"""
        return self.database_url(self.meta_database)


@dataclass(frozen=True)
class PostgresConfig:
    """Agent 平台 PostgreSQL 连接配置。"""

    host: str
    port: int
    user: str
    password: str
    database: str
    pool_pre_ping: bool
    pool_recycle: int

    def _credentials(self) -> str:
        """返回适合放入连接 URL 的账号部分。"""
        from urllib.parse import quote_plus

        user = quote_plus(self.user)
        if not self.password:
            return user
        return f"{user}:{quote_plus(self.password)}"

    @property
    def database_url(self) -> str:
        """返回 SQLAlchemy 使用的异步 PostgreSQL URL。"""
        return (
            f"postgresql+psycopg://{self._credentials()}"
            f"@{self.host}:{self.port}/{self.database}"
        )

@dataclass(frozen=True)
class Neo4jConfig:
    """Semantic Memory 使用的 Neo4j 图数据库配置。"""

    uri: str
    user: str
    password: str
    database: str
    max_connection_pool_size: int


@dataclass(frozen=True)
class QdrantConfig:
    """Qdrant 向量数据库配置。"""

    url: str
    tables_collection: str
    columns_collection: str
    metrics_collection: str
    dimension_values_collection: str
    memory_episodic_collection: str
    memory_semantic_collection: str
    memory_perceptual_text_collection: str
    memory_perceptual_image_collection: str
    memory_perceptual_audio_collection: str
    memory_perceptual_video_collection: str
    vector_size: int
    distance: str
    upsert_batch_size: int


@dataclass(frozen=True)
class EmbeddingConfig:
    """Embedding 服务配置；provider 决定走云端兼容接口还是本地推理服务。"""

    provider: str
    model_name: str
    dimensions: int
    api_key: str
    base_url: str
    batch_size: int
    # 单次 embedding 调用的硬超时；推理服务挂起时快速失败而不是无限等待。
    timeout_seconds: float
    # 以下字段仅在 provider: local 时使用。
    host: str
    port: int
    model: str

    @property
    def active_model(self) -> str:
        """当前生效的模型标识，用于记忆编码器命名等场景。"""
        return self.model_name if self.provider == "openai-compatible" else self.model


@dataclass(frozen=True)
class ElasticsearchConfig:
    """Elasticsearch 全文检索配置。"""

    url: str
    dimension_values_alias: str
    dimension_values_index: str
    bulk_batch_size: int


@dataclass(frozen=True)
class DimensionValueSearchConfig:
    """维度值混合检索的范围、召回数量和融合参数。"""

    included_dimensions: tuple[str, ...]
    es_top_k_per_term: int
    vector_top_k_per_term: int
    term_max_k: int
    total_max_k: int
    vector_score_threshold: float
    rrf_k: int


@dataclass(frozen=True)
class MetadataRecallConfig:
    """问数元数据召回的统一并发和规模边界。"""

    # 每类元数据最多使用多少个召回词，避免一次问题放大成过多外部请求。
    max_recall_terms: int
    # 同一类元数据同时执行的 Embedding/向量或全文请求数量上限。
    max_concurrent_terms: int


@dataclass(frozen=True)
class LlmConfig:
    """LLM 调用配置。"""

    provider: str
    model_name: str
    api_key: str
    base_url: str
    timeout_seconds: float
    max_tokens: int | None
    request_options: dict[str, Any]
    include_usage: bool
    stream_only: bool


@dataclass(frozen=True)
class LoggingConfig:
    """日志配置。"""

    level: str
    format: str


@dataclass(frozen=True)
class Settings:
    """项目所有配置的聚合对象。"""

    app: AppConfig
    harness: HarnessConfig
    mysql: MysqlConfig
    postgres: PostgresConfig
    neo4j: Neo4jConfig
    qdrant: QdrantConfig
    elasticsearch: ElasticsearchConfig
    dimension_value_search: DimensionValueSearchConfig
    metadata_recall: MetadataRecallConfig
    embedding: EmbeddingConfig
    llm: LlmConfig
    logging: LoggingConfig


def _load_dotenv(path: Path | None = None) -> None:
    """读取项目根目录 `.env` 到当前进程环境变量。"""
    dotenv_path = path or (PROJECT_ROOT / ".env")
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def _load_yaml_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """读取 YAML 配置文件，并通过 OmegaConf 解析环境变量占位。"""
    _load_dotenv()
    raw = OmegaConf.to_container(OmegaConf.load(path), resolve=True)
    if not isinstance(raw, dict):
        raise TypeError("config.yaml 解析结果不是字典。")
    return raw


def load_settings(path: Path = CONFIG_PATH) -> Settings:
    """把 YAML 配置转换成带类型的 Settings 对象。"""
    raw = _load_yaml_config(path)
    search_config = raw["dimension_value_search"]
    return Settings(
        app=AppConfig(**raw["app"]),
        harness=HarnessConfig(**raw["harness"]),
        mysql=MysqlConfig(**raw["mysql"]),
        postgres=PostgresConfig(**raw["postgres"]),
        neo4j=Neo4jConfig(**raw["neo4j"]),
        qdrant=QdrantConfig(**raw["qdrant"]),
        elasticsearch=ElasticsearchConfig(**raw["elasticsearch"]),
        dimension_value_search=DimensionValueSearchConfig(
            included_dimensions=tuple(search_config["included_dimensions"]),
            es_top_k_per_term=search_config["es_top_k_per_term"],
            vector_top_k_per_term=search_config["vector_top_k_per_term"],
            term_max_k=search_config["term_max_k"],
            total_max_k=search_config["total_max_k"],
            vector_score_threshold=search_config["vector_score_threshold"],
            rrf_k=search_config["rrf_k"],
        ),
        metadata_recall=MetadataRecallConfig(**raw["metadata_recall"]),
        embedding=EmbeddingConfig(**raw["embedding"]),
        llm=LlmConfig(**raw["llm"]),
        logging=LoggingConfig(**raw["logging"]),
    )


settings = load_settings()
