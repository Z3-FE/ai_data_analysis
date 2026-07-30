"""指标元数据向量构建服务测试。"""

import unittest
from unittest.mock import patch

from app.services.semantic.metric_vector_service import (
    build_meta_metric_vectors,
    build_metric_vector_documents,
)


def make_metric() -> dict:
    """返回一条与 meta.metrics 字段一致的测试记录。"""
    return {
        "metric_id": "gmv",
        "metric_name": "gmv",
        "business_name": "销售额",
        "base_table_id": "dw.fact_order_item",
        "expression_sql": "SUM(price)",
        "aggregation_type": "sum",
        "unit": "currency",
        "description": "商品成交金额之和，第一版口径不含运费。",
        "aliases": '["GMV", "成交金额", "商品成交额"]',
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


class MetricVectorServiceTest(unittest.TestCase):
    """验证指标元数据拆分、稳定 ID 和写入统计。"""

    def test_one_metric_builds_four_vector_documents(self) -> None:
        documents = build_metric_vector_documents(make_metric())

        self.assertEqual(len(documents), 4)
        self.assertEqual(
            {document.vector_type for document in documents},
            {"metric_name", "business_name", "description", "aliases"},
        )
        self.assertEqual(len({document.point_id for document in documents}), 4)
        self.assertTrue(
            all(document.payload["metric_id"] == "gmv" for document in documents)
        )

    @patch(
        "app.services.semantic.metric_vector_service.list_active_metrics_for_embedding",
        return_value=[make_metric()],
    )
    def test_build_writes_all_points(self, _) -> None:
        qdrant_repository = FakeQdrantRepository()

        result = build_meta_metric_vectors(
            db=object(),
            embedding_client=FakeEmbeddingClient(),
            qdrant_repository=qdrant_repository,
        )

        self.assertEqual(result["metric_count"], 1)
        self.assertEqual(result["point_count"], 4)
        self.assertEqual(result["qdrant_count"], 4)
        self.assertEqual(qdrant_repository.vector_size, 1024)
        self.assertIn("metric_id", qdrant_repository.payload_indexes)


if __name__ == "__main__":
    unittest.main()
