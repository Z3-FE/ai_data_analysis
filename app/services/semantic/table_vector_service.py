"""meta.tables 到 Qdrant 表语义向量的构建服务。

一条 meta.tables 记录会拆成多个关键文本分别向量化：business_name、table_name、
description、grain、aliases。payload 保留完整元数据，便于检索后回溯来源。
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any

from qdrant_client.http.models import PointStruct
from sqlalchemy.orm import Session

from app.clients.embedding_client import EmbeddingClient
from app.core.config import settings
from app.repositories.meta_repository import list_active_tables_for_embedding
from app.repositories.qdrant_repository import QdrantRepository

POINT_NAMESPACE = uuid.UUID("38f6ec71-94a6-47d9-8d73-c5900ed2a141")


@dataclass(frozen=True)
class TableVectorDocument:
    """一条待写入 Qdrant 的表向量文档。"""

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
    """根据可读 point_key 生成稳定 UUID，避免重复构建产生不同 ID。"""
    return str(uuid.uuid5(POINT_NAMESPACE, point_key))


def build_table_vector_documents(table: dict[str, Any]) -> list[TableVectorDocument]:
    """把一条 meta.tables 记录拆成多个向量文档。"""
    aliases = _parse_aliases(table.get("aliases"))
    payload = {
        "table_id": table["table_id"],
        "data_source_id": table["data_source_id"],
        "database_name": table["database_name"],
        "table_name": table["table_name"],
        "table_type": table["table_type"],
        "business_name": table["business_name"],
        "grain": table["grain"],
        "description": table["description"],
        "aliases": aliases,
        "status": table["status"],
    }

    text_units = [
        ("business_name", f"表业务名称：{table['business_name']}"),
        ("table_name", f"表物理名称：{table['table_name']}"),
        ("description", f"表业务说明：{table['description']}"),
        ("grain", f"表数据粒度：{table['grain']}"),
    ]
    if aliases:
        text_units.append(("aliases", f"表别名：{'、'.join(aliases)}"))

    documents = []
    for vector_type, text in text_units:
        point_key = f"{table['table_id']}::{vector_type}"
        documents.append(
            TableVectorDocument(
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


def build_meta_table_vectors(
    db: Session,
    embedding_client: EmbeddingClient,
    qdrant_repository: QdrantRepository,
) -> dict[str, Any]:
    """构建 meta.tables 的 Qdrant 向量数据。"""
    tables = list_active_tables_for_embedding(db)
    if not tables:
        raise ValueError("meta.tables 中没有 status=active 的表元数据。")

    documents = [
        document
        for table in tables
        for document in build_table_vector_documents(table)
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

    collection_name = settings.qdrant.tables_collection
    qdrant_repository.recreate_collection(
        collection_name=collection_name,
        vector_size=settings.qdrant.vector_size,
        distance=settings.qdrant.distance,
        payload_indexes=("table_id", "status", "vector_type"),
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
        "table_count": len(tables),
        "point_count": len(points),
        "qdrant_count": qdrant_repository.get_collection_count(collection_name),
    }
