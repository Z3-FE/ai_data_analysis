"""meta.metrics 到 Qdrant 指标语义向量的构建服务。

一条 meta.metrics 记录拆成 metric_name、business_name、description、aliases
四个关键文本分别向量化。计算表达式和聚合方式保留在 payload 中作为权威口径。
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any

from qdrant_client.http.models import PointStruct
from sqlalchemy.orm import Session

from app.core.config import settings
from app.repositories.meta_build_test_repository import (
    list_active_metrics_for_embedding,
)
from app.repositories.qdrant_repository import QdrantRepository
from app.services.semantic.embedding_batch import embed_documents_in_batches

METRIC_POINT_NAMESPACE = uuid.UUID("94961fdc-adf9-468d-abd8-c88e1503279b")


@dataclass(frozen=True)
class MetricVectorDocument:
    """一条待写入 Qdrant 的指标向量文档。"""

    point_id: str
    point_key: str
    vector_type: str
    text: str
    payload: dict[str, Any]


def _parse_aliases(raw_aliases: Any) -> list[str]:
    """把 MySQL JSON 字段转换成 Python 字符串列表。"""
    if raw_aliases is None:
        return []
    if isinstance(raw_aliases, list):
        return [str(item) for item in raw_aliases]
    if isinstance(raw_aliases, str):
        parsed = json.loads(raw_aliases)
        return [str(item) for item in parsed]
    return []


def _stable_point_id(point_key: str) -> str:
    """根据可读 point_key 生成稳定 UUID。"""
    return str(uuid.uuid5(METRIC_POINT_NAMESPACE, point_key))


def build_metric_vector_documents(metric: dict[str, Any]) -> list[MetricVectorDocument]:
    """把一条 meta.metrics 记录拆成多个向量文档。"""
    aliases = _parse_aliases(metric.get("aliases"))
    payload = {
        "metric_id": metric["metric_id"],
        "metric_name": metric["metric_name"],
        "business_name": metric["business_name"],
        "base_table_id": metric["base_table_id"],
        "expression_sql": metric["expression_sql"],
        "aggregation_type": metric["aggregation_type"],
        "calculation_grain": metric.get("calculation_grain", ""),
        "aggregation_rule": metric.get("aggregation_rule", ""),
        "unit": metric["unit"],
        "description": metric["description"],
        "aliases": aliases,
        "status": metric["status"],
    }

    text_units = [
        ("metric_name", f"指标物理名称：{metric['metric_name']}"),
        ("business_name", f"指标业务名称：{metric['business_name']}"),
        (
            "description",
            "指标业务口径："
            f"{metric['description']}；计算粒度：{metric.get('calculation_grain', '')}；"
            f"聚合规则：{metric.get('aggregation_rule', '')}",
        ),
    ]
    if aliases:
        text_units.append(("aliases", f"指标别名：{'、'.join(aliases)}"))

    documents = []
    for vector_type, text in text_units:
        point_key = f"{metric['metric_id']}::{vector_type}"
        documents.append(
            MetricVectorDocument(
                point_id=_stable_point_id(point_key),
                point_key=point_key,
                vector_type=vector_type,
                text=text,
                payload={
                    **payload,
                    "vector_type": vector_type,
                    "point_key": point_key,
                    "text": text,
                },
            )
        )
    return documents


def build_meta_metric_vectors(
    db: Session,
    embedding_client,
    qdrant_repository: QdrantRepository,
) -> dict[str, Any]:
    """构建 meta.metrics 的 Qdrant 向量数据。"""
    metrics = list_active_metrics_for_embedding(db)
    if not metrics:
        raise ValueError("meta.metrics 中没有 status=active 的指标元数据。")

    documents = [
        document
        for metric in metrics
        for document in build_metric_vector_documents(metric)
    ]
    vectors = embed_documents_in_batches(
        embedding_client,
        [document.text for document in documents],
        settings.embedding.batch_size,
    )

    if len(vectors) != len(documents):
        raise ValueError("Embedding 返回向量数量与待写入文档数量不一致。")

    invalid_sizes = {
        len(vector)
        for vector in vectors
        if len(vector) != settings.qdrant.vector_size
    }
    if invalid_sizes:
        raise ValueError(
            "Embedding 向量维度与 Qdrant 配置不一致："
            f"期望 {settings.qdrant.vector_size}，实际发现 {sorted(invalid_sizes)}。"
        )

    collection_name = settings.qdrant.metrics_collection
    qdrant_repository.recreate_collection(
        collection_name=collection_name,
        vector_size=settings.qdrant.vector_size,
        distance=settings.qdrant.distance,
        payload_indexes=(
            "metric_id",
            "base_table_id",
            "aggregation_type",
            "status",
            "vector_type",
        ),
    )

    points = [
        PointStruct(
            id=document.point_id,
            vector=vector,
            payload=document.payload,
        )
        for document, vector in zip(documents, vectors, strict=True)
    ]
    qdrant_repository.upsert_points(collection_name, points)

    return {
        "collection": collection_name,
        "metric_count": len(metrics),
        "point_count": len(points),
        "qdrant_count": qdrant_repository.get_collection_count(collection_name),
    }
