"""meta.columns 到 Qdrant 字段语义向量的构建服务。

一条 meta.columns 记录拆成 column_name、business_name、description、aliases
四个关键文本分别向量化。payload 保留完整字段元数据，便于检索后定位真实字段。
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any

from qdrant_client.http.models import PointStruct
from sqlalchemy.orm import Session

from app.clients.embedding_client import EmbeddingClient
from app.core.config import settings
from app.repositories.meta_repository import list_active_columns_for_embedding
from app.repositories.qdrant_repository import QdrantRepository

COLUMN_POINT_NAMESPACE = uuid.UUID("352141de-1c1d-4846-bf45-8e06e5d247a8")


@dataclass(frozen=True)
class ColumnVectorDocument:
    """一条待写入 Qdrant 的字段向量文档。"""

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
    return str(uuid.uuid5(COLUMN_POINT_NAMESPACE, point_key))


def build_column_vector_documents(column: dict[str, Any]) -> list[ColumnVectorDocument]:
    """把一条 meta.columns 记录拆成多个向量文档。"""
    aliases = _parse_aliases(column.get("aliases"))
    payload = {
        "column_id": column["column_id"],
        "table_id": column["table_id"],
        "column_name": column["column_name"],
        "business_name": column["business_name"],
        "data_type": column["data_type"],
        "semantic_role": column["semantic_role"],
        "is_queryable": column["is_queryable"],
        "is_aggregatable": column["is_aggregatable"],
        "description": column["description"],
        "aliases": aliases,
        "status": column["status"],
    }

    text_units = [
        ("column_name", f"字段物理名称：{column['column_name']}"),
        ("business_name", f"字段业务名称：{column['business_name']}"),
        ("description", f"字段业务说明：{column['description']}"),
    ]
    if aliases:
        text_units.append(("aliases", f"字段别名：{'、'.join(aliases)}"))

    documents = []
    for vector_type, text in text_units:
        point_key = f"{column['column_id']}::{vector_type}"
        documents.append(
            ColumnVectorDocument(
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


def build_meta_column_vectors(
    db: Session,
    embedding_client: EmbeddingClient,
    qdrant_repository: QdrantRepository,
) -> dict[str, Any]:
    """构建 meta.columns 的 Qdrant 向量数据。"""
    columns = list_active_columns_for_embedding(db)
    if not columns:
        raise ValueError("meta.columns 中没有启用且允许查询的字段元数据。")

    documents = [
        document
        for column in columns
        for document in build_column_vector_documents(column)
    ]
    vectors = embedding_client.embed_texts([document.text for document in documents])

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

    collection_name = settings.qdrant.columns_collection
    qdrant_repository.recreate_collection(
        collection_name=collection_name,
        vector_size=settings.qdrant.vector_size,
        distance=settings.qdrant.distance,
        payload_indexes=(
            "column_id",
            "table_id",
            "semantic_role",
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
        "column_count": len(columns),
        "point_count": len(points),
        "qdrant_count": qdrant_repository.get_collection_count(collection_name),
    }
