"""字段元数据向量构建服务测试。"""

import unittest
from unittest.mock import patch

from app.services.semantic.column_vector_service import (
    build_column_vector_documents,
    build_meta_column_vectors,
)


def make_column() -> dict:
    """返回一条与 meta.columns 字段一致的测试记录。"""
    return {
        "column_id": "dw.fact_order.order_count",
        "table_id": "dw.fact_order",
        "table_name": "fact_order",
        "column_name": "order_count",
        "business_name": "订单数",
        "data_type": "INT",
        "semantic_role": "measure",
        "is_queryable": 1,
        "is_aggregatable": 1,
        "description": "每行固定为 1，用于统计订单数。",
        "aliases": '["订单量", "下单数", "订单数量"]',
        "status": "active",
    }


class FakeEmbeddingClient:
    """返回固定 1024 维向量。"""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 1024 for _ in texts]


class FakeQdrantRepository:
    """记录 Qdrant 调用参数。"""

    def __init__(self) -> None:
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


class ColumnVectorServiceTest(unittest.TestCase):
    """验证字段元数据拆分、稳定 ID 和写入统计。"""

    def test_one_column_builds_four_vector_documents(self) -> None:
        documents = build_column_vector_documents(make_column())

        self.assertEqual(len(documents), 4)
        self.assertEqual(
            {document.vector_type for document in documents},
            {"column_name", "business_name", "description", "aliases"},
        )
        self.assertEqual(len({document.point_id for document in documents}), 4)
        self.assertTrue(
            all(
                document.payload["column_id"] == "dw.fact_order.order_count"
                for document in documents
            )
        )

    @patch(
        "app.services.semantic.column_vector_service.list_active_columns_for_embedding",
        return_value=[make_column()],
    )
    def test_build_writes_all_points(self, _) -> None:
        qdrant_repository = FakeQdrantRepository()

        result = build_meta_column_vectors(
            db=object(),
            embedding_client=FakeEmbeddingClient(),
            qdrant_repository=qdrant_repository,
        )

        self.assertEqual(result["column_count"], 1)
        self.assertEqual(result["point_count"], 4)
        self.assertEqual(result["qdrant_count"], 4)
        self.assertEqual(qdrant_repository.vector_size, 1024)
        self.assertIn("column_id", qdrant_repository.payload_indexes)


if __name__ == "__main__":
    unittest.main()
