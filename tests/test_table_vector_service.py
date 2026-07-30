"""表元数据向量构建服务测试。"""

import unittest
from unittest.mock import patch

from app.services.semantic.table_vector_service import (
    build_meta_table_vectors,
    build_table_vector_documents,
)


def make_table() -> dict:
    """返回一条与 meta.tables 字段一致的测试记录。"""
    return {
        "table_id": "dw.fact_order",
        "data_source_id": "olist_dw",
        "database_name": "dw",
        "table_name": "fact_order",
        "table_type": "fact",
        "business_name": "订单事实",
        "grain": "一个订单一行",
        "description": "用于订单量、状态、履约和物流时效分析。",
        "aliases": '["订单表", "订单主表"]',
        "status": "active",
    }


class FakeEmbeddingClient:
    """返回固定 1024 维向量，避免测试依赖真实 TEI 服务。"""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 1024 for _ in texts]


class FakeQdrantRepository:
    """记录 Qdrant 调用参数，避免测试依赖真实 Qdrant。"""

    def __init__(self) -> None:
        self.collection_name = ""
        self.points = []

    def recreate_collection(
        self,
        collection_name: str,
        vector_size: int,
        distance: str,
        payload_indexes: tuple[str, ...] = (),
    ) -> None:
        self.collection_name = collection_name
        self.vector_size = vector_size
        self.distance = distance
        self.payload_indexes = payload_indexes

    def upsert_points(self, collection_name: str, points: list) -> None:
        self.collection_name = collection_name
        self.points = points

    def get_collection_count(self, collection_name: str) -> int:
        return len(self.points)


class TableVectorServiceTest(unittest.TestCase):
    """验证表元数据拆分、稳定 ID 和写入统计。"""

    def test_one_table_builds_five_vector_documents(self) -> None:
        documents = build_table_vector_documents(make_table())

        self.assertEqual(len(documents), 5)
        self.assertEqual(
            {document.vector_type for document in documents},
            {"business_name", "table_name", "description", "grain", "aliases"},
        )
        self.assertEqual(len({document.point_id for document in documents}), 5)
        self.assertTrue(
            all(document.payload["table_id"] == "dw.fact_order" for document in documents)
        )

    @patch(
        "app.services.semantic.table_vector_service.list_active_tables_for_embedding",
        return_value=[make_table()],
    )
    def test_build_writes_all_points(self, _) -> None:
        qdrant_repository = FakeQdrantRepository()

        result = build_meta_table_vectors(
            db=object(),
            embedding_client=FakeEmbeddingClient(),
            qdrant_repository=qdrant_repository,
        )

        self.assertEqual(result["table_count"], 1)
        self.assertEqual(result["point_count"], 5)
        self.assertEqual(result["qdrant_count"], 5)
        self.assertEqual(qdrant_repository.vector_size, 1024)


if __name__ == "__main__":
    unittest.main()
