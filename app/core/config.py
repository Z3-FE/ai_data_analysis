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
class QdrantConfig:
    """Qdrant 向量数据库配置。"""

    url: str
    tables_collection: str
    columns_collection: str
    metrics_collection: str
    dimension_values_collection: str
    vector_size: int
    distance: str
    upsert_batch_size: int


@dataclass(frozen=True)
class EmbeddingConfig:
    """Embedding 推理服务配置。"""

    host: str
    port: int
    model: str
    batch_size: int
    retry_count: int
    retry_backoff_seconds: float


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
    mysql: MysqlConfig
    qdrant: QdrantConfig
    elasticsearch: ElasticsearchConfig
    dimension_value_search: DimensionValueSearchConfig
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
        mysql=MysqlConfig(**raw["mysql"]),
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
        embedding=EmbeddingConfig(**raw["embedding"]),
        llm=LlmConfig(**raw["llm"]),
        logging=LoggingConfig(**raw["logging"]),
    )


settings = load_settings()
