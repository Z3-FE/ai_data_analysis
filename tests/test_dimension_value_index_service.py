"""维度值混合索引构建服务测试。"""

import unittest
from unittest.mock import patch

from app.services.semantic.dimension_value_index_service import (
    build_dimension_value_indexes,
    build_dimension_value_vector_documents,
    build_es_document,
)


def make_dimension_value() -> dict:
    """返回一条与 meta.dimension_values 查询结果一致的测试记录。"""
    return {
        "value_id": "dw.dim_payment_type.payment_type::credit_card",
        "dimension_id": "payment_type",
        "dimension_name": "payment_type",
        "dimension_business_name": "支付方式",
        "column_id": "dw.dim_payment_type.payment_type",
        "column_name": "payment_type",
        "data_type": "VARCHAR(32)",
        "table_id": "dw.dim_payment_type",
        "table_name": "dim_payment_type",
        "database_name": "dw",
        "raw_value": "credit_card",
        "normalized_value": "credit card",
        "display_name": "信用卡支付",
        "aliases": '["信用卡", "刷卡支付"]',
        "description": "使用信用卡完成支付。",
        "value_count": 100,
        "semantic_enabled": 1,
        "status": "active",
    }


class FakeEmbeddingClient:
    """返回固定 1024 维向量。"""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 1024 for _ in texts]


class FakeQdrantRepository:
    """记录 Qdrant 构建参数。"""

    def __init__(self) -> None:
        self.points = []

    def create_collection_if_not_exists(self, **kwargs) -> None:
        self.collection_name = kwargs["collection_name"]
        self.payload_indexes = kwargs["payload_indexes"]

    def point_exists(self, collection_name: str, point_id: str) -> bool:
        return False

    def upsert_points(self, collection_name: str, points: list) -> None:
        self.points.extend(points)

    def get_collection_count(self, collection_name: str) -> int:
        return len(self.points)


class FakeElasticsearchRepository:
    """记录 Elasticsearch 构建参数。"""

    def recreate_dimension_values_index(self, index_name: str) -> None:
        self.index_name = index_name

    def bulk_index(self, index_name: str, documents: list) -> int:
        self.documents = documents
        return len(documents)

    def switch_alias(self, alias_name: str, index_name: str) -> None:
        self.alias_name = alias_name

    def count(self, index_name: str) -> int:
        return len(self.documents)


class DimensionValueIndexServiceTest(unittest.TestCase):
    """验证 ES 文档、向量拆分和双索引写入。"""

    def test_builds_es_document_with_parsed_aliases(self) -> None:
        document = build_es_document(make_dimension_value())

        self.assertEqual(document["raw_value"], "credit_card")
        self.assertEqual(document["aliases"], ["信用卡", "刷卡支付"])
        self.assertTrue(document["semantic_enabled"])

    def test_one_value_builds_separate_alias_vectors(self) -> None:
        documents = build_dimension_value_vector_documents(make_dimension_value())

        self.assertEqual(len(documents), 5)
        self.assertEqual(
            [document.vector_type for document in documents].count("alias"),
            2,
        )
        self.assertEqual(len({document.point_id for document in documents}), 5)
        self.assertTrue(all("point_key" in document.payload for document in documents))

    @patch(
        "app.services.semantic.dimension_value_index_service.list_active_dimension_values",
        return_value=[make_dimension_value()],
    )
    def test_builds_elasticsearch_and_qdrant(self, _) -> None:
        qdrant_repository = FakeQdrantRepository()
        es_repository = FakeElasticsearchRepository()

        result = build_dimension_value_indexes(
            db=object(),
            embedding_client=FakeEmbeddingClient(),
            qdrant_repository=qdrant_repository,
            es_repository=es_repository,
        )

        self.assertEqual(result["value_count"], 1)
        self.assertEqual(result["elasticsearch"]["document_count"], 1)
        self.assertEqual(result["qdrant"]["point_count"], 5)
        self.assertEqual(result["qdrant"]["created_point_count"], 5)
        self.assertIn("column_id", qdrant_repository.payload_indexes)


if __name__ == "__main__":
    unittest.main()
