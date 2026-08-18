"""统一构建 Elasticsearch 和 Qdrant 维度值索引。

运行方式：
  uv run python -m app.scripts.build_dimension_value_indexes
"""

import json

from elasticsearch import Elasticsearch
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.clients.embedding_client import embedding_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.core.config import settings
from app.core.logging import setup_logging
from app.repositories.elasticsearch_repository import ElasticsearchRepository
from app.repositories.qdrant_repository import QdrantRepository
from app.services.semantic.dimension_value_index_service import (
    build_dimension_value_indexes,
)


def _require_initialized(resource, resource_name: str):
    """确保脚本级客户端已经初始化。"""
    if resource is None:
        raise RuntimeError(f"{resource_name}尚未初始化")
    return resource


def main() -> None:
    """脚本入口：读取 MySQL meta 并输出两套索引构建统计。"""
    setup_logging()
    embedding_client_manager.init()
    qdrant_client_manager.init()
    engine = create_engine(settings.mysql.meta_database_url)
    es_client = Elasticsearch(settings.elasticsearch.url)
    try:
        embedding_client = _require_initialized(
            embedding_client_manager.client,
            "Embedding 客户端",
        )
        qdrant_client = _require_initialized(
            qdrant_client_manager.client,
            "Qdrant 客户端",
        )
        with Session(engine) as session:
            result = build_dimension_value_indexes(
                session,
                embedding_client=embedding_client,
                qdrant_repository=QdrantRepository(qdrant_client),
                es_repository=ElasticsearchRepository(es_client),
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        es_client.close()
        engine.dispose()


if __name__ == "__main__":
    main()
