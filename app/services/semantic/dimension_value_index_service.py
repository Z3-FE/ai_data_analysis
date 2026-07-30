"""维度值的 Elasticsearch 全文索引和 Qdrant 语义索引构建服务。

MySQL `meta.dimension_values` 是数据真源。一条记录在 ES 中对应一条文档，在
Qdrant 中按标准名称、每个别名、说明和有语义的真实值拆成多个向量点。
"""

import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

from qdrant_client.http.models import PointStruct
from sqlalchemy.orm import Session

from app.core.config import settings
from app.repositories.elasticsearch_repository import ElasticsearchRepository
from app.repositories.meta_repository import list_active_dimension_values
from app.repositories.qdrant_repository import QdrantRepository

DIMENSION_VALUE_POINT_NAMESPACE = uuid.UUID("c8ce956d-88d2-4fe7-a7e7-44548fc0343f")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DimensionValueVectorDocument:
    """一条待写入 Qdrant 的维度值向量文档。"""

    point_id: str
    point_key: str
    vector_type: str
    text: str
    payload: dict[str, Any]


def parse_aliases(raw_aliases: Any) -> list[str]:
    """把 MySQL JSON aliases 转成去重后的字符串列表。"""
    if raw_aliases is None:
        return []
    values = json.loads(raw_aliases) if isinstance(raw_aliases, str) else raw_aliases
    if not isinstance(values, list):
        return []
    return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def build_es_document(value: dict[str, Any]) -> dict[str, Any]:
    """把一条 MySQL 维度值转换成 ES 文档。"""
    return {
        "value_id": value["value_id"],
        "dimension_id": value["dimension_id"],
        "dimension_name": value["dimension_name"],
        "dimension_business_name": value["dimension_business_name"],
        "column_id": value["column_id"],
        "column_name": value["column_name"],
        "table_id": value["table_id"],
        "table_name": value["table_name"],
        "database_name": value["database_name"],
        "data_type": value["data_type"],
        "raw_value": value["raw_value"],
        "normalized_value": value["normalized_value"],
        "display_name": value["display_name"],
        "aliases": parse_aliases(value.get("aliases")),
        "description": value["description"],
        "value_count": int(value["value_count"]),
        "semantic_enabled": bool(value["semantic_enabled"]),
        "status": value["status"],
    }


def _stable_point_id(point_key: str) -> str:
    """根据可读 point_key 生成稳定 UUID。"""
    return str(uuid.uuid5(DIMENSION_VALUE_POINT_NAMESPACE, point_key))


def _has_semantic_text(text: str) -> bool:
    """判断真实值是否包含字母或中文，纯数字和短代码不单独向量化。"""
    return bool(re.search(r"[A-Za-z\u4e00-\u9fff]", text)) and not (
        len(text) <= 3 and text.isupper()
    )


def build_dimension_value_vector_documents(
    value: dict[str, Any],
) -> list[DimensionValueVectorDocument]:
    """把一条维度值记录拆成多个独立语义向量文档。"""
    if not bool(value["semantic_enabled"]):
        return []

    aliases = parse_aliases(value.get("aliases"))
    payload = build_es_document(value)
    text_units: list[tuple[str, str, str]] = [
        (
            "display_name",
            "0",
            f"{value['dimension_business_name']}的标准值：{value['display_name']}",
        )
    ]
    text_units.extend(
        (
            "alias",
            str(index),
            f"{value['dimension_business_name']}的常见说法：{alias}",
        )
        for index, alias in enumerate(aliases)
    )

    normalized_value = str(value["normalized_value"]).strip()
    if _has_semantic_text(normalized_value):
        text_units.append(
            (
                "normalized_value",
                "0",
                f"{value['dimension_business_name']}的数据库值：{normalized_value}",
            )
        )
    description = str(value["description"]).strip()
    if description:
        text_units.append(("description", "0", description))

    documents = []
    for vector_type, sequence, text in text_units:
        point_key = f"{value['value_id']}::{vector_type}::{sequence}"
        documents.append(
            DimensionValueVectorDocument(
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


def _validate_vectors(vectors: list[list[float]], expected_count: int) -> None:
    """校验 Embedding 返回数量和向量维度。"""
    if len(vectors) != expected_count:
        raise ValueError("Embedding 返回向量数量与维度值文档数量不一致。")
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


def _build_qdrant_dimension_value_vectors(
    vector_documents: list[DimensionValueVectorDocument],
    embedding_client,
    qdrant_repository: QdrantRepository,
) -> dict[str, Any]:
    """分批生成并写入 Qdrant，支持中断后按 point_id 续建。"""
    collection_name = settings.qdrant.dimension_values_collection
    payload_indexes = (
        "value_id",
        "dimension_id",
        "column_id",
        "table_id",
        "status",
        "vector_type",
    )
    qdrant_repository.create_collection_if_not_exists(
        collection_name=collection_name,
        vector_size=settings.qdrant.vector_size,
        distance=settings.qdrant.distance,
        payload_indexes=payload_indexes,
    )

    batch_size = settings.qdrant.upsert_batch_size
    created_points = 0
    skipped_points = 0
    for start in range(0, len(vector_documents), batch_size):
        batch = vector_documents[start : start + batch_size]
        pending = [
            document
            for document in batch
            if not qdrant_repository.point_exists(collection_name, document.point_id)
        ]
        skipped_points += len(batch) - len(pending)
        if not pending:
            continue
        logger.info(
            "生成并写入维度值向量 %s-%s / %s",
            start + 1,
            start + len(batch),
            len(vector_documents),
        )
        vectors = embedding_client.embed_documents([document.text for document in pending])
        _validate_vectors(vectors, len(pending))
        points = [
            PointStruct(
                id=document.point_id,
                vector=vector,
                payload=document.payload,
            )
            for document, vector in zip(pending, vectors, strict=True)
        ]
        qdrant_repository.upsert_points(collection_name, points)
        created_points += len(points)

    return {
        "collection": collection_name,
        "point_count": len(vector_documents),
        "created_point_count": created_points,
        "skipped_point_count": skipped_points,
        "collection_count": qdrant_repository.get_collection_count(collection_name),
    }


def build_dimension_value_indexes(
    db: Session,
    embedding_client,
    qdrant_repository: QdrantRepository,
    es_repository: ElasticsearchRepository,
) -> dict[str, Any]:
    """构建维度值的 ES 全文索引和 Qdrant 语义索引。"""
    values = list_active_dimension_values(db, settings.dimension_value_search.included_dimensions)
    if not values:
        raise ValueError("meta.dimension_values 中没有可索引的维度值。")

    es_documents = [build_es_document(value) for value in values]
    index_name = settings.elasticsearch.dimension_values_index
    alias_name = settings.elasticsearch.dimension_values_alias
    es_repository.recreate_dimension_values_index(index_name)
    document_count = es_repository.bulk_index(index_name, es_documents)
    es_repository.switch_alias(alias_name, index_name)

    vector_documents = [
        document
        for value in values
        for document in build_dimension_value_vector_documents(value)
    ]
    qdrant_result = _build_qdrant_dimension_value_vectors(
        vector_documents=vector_documents,
        embedding_client=embedding_client,
        qdrant_repository=qdrant_repository,
    )

    return {
        "value_count": len(values),
        "elasticsearch": {
            "index_name": index_name,
            "alias_name": alias_name,
            "document_count": document_count,
            "alias_switched": True,
        },
        "qdrant": qdrant_result,
    }
