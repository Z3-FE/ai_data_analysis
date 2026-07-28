"""统一构建表、字段和指标的 Qdrant 语义向量。

运行方式：
  uv run python -m app.scripts.build_meta_vectors
"""

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from app.clients.embedding_client import EmbeddingClient
from app.clients.mysql_client import MetaSessionLocal
from app.clients.qdrant_client import VectorDbClient
from app.core.logging import setup_logging
from app.repositories.qdrant_repository import QdrantRepository
from app.services.semantic.column_vector_service import build_meta_column_vectors
from app.services.semantic.metric_vector_service import build_meta_metric_vectors
from app.services.semantic.table_vector_service import build_meta_table_vectors

logger = logging.getLogger(__name__)


def build_all_meta_vectors(
    db: Session,
    embedding_client: EmbeddingClient,
    qdrant_repository: QdrantRepository,
) -> dict[str, Any]:
    """按表、字段、指标顺序构建全部元数据向量。"""
    logger.info("开始构建表元数据向量")
    tables_result = build_meta_table_vectors(
        db,
        embedding_client=embedding_client,
        qdrant_repository=qdrant_repository,
    )

    logger.info("开始构建字段元数据向量")
    columns_result = build_meta_column_vectors(
        db,
        embedding_client=embedding_client,
        qdrant_repository=qdrant_repository,
    )

    logger.info("开始构建指标元数据向量")
    metrics_result = build_meta_metric_vectors(
        db,
        embedding_client=embedding_client,
        qdrant_repository=qdrant_repository,
    )

    return {
        "tables": tables_result,
        "columns": columns_result,
        "metrics": metrics_result,
        "total_point_count": (
            tables_result["point_count"]
            + columns_result["point_count"]
            + metrics_result["point_count"]
        ),
        "qdrant_total_count": (
            tables_result["qdrant_count"]
            + columns_result["qdrant_count"]
            + metrics_result["qdrant_count"]
        ),
    }


def main() -> None:
    """脚本入口：统一构建并输出三类元数据向量统计。"""
    setup_logging()
    embedding_client = EmbeddingClient()
    qdrant_repository = QdrantRepository(client=VectorDbClient())
    with MetaSessionLocal() as db:
        result = build_all_meta_vectors(
            db,
            embedding_client=embedding_client,
            qdrant_repository=qdrant_repository,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
