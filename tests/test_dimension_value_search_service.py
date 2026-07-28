"""维度值混合检索与 RRF 融合测试。"""

import unittest

from app.services.semantic.dimension_value_search_service import (
    DimensionValueSearchService,
    merge_dimension_value_results,
)


def make_source(raw_value: str, display_name: str) -> dict:
    """创建可被检索服务融合的完整来源数据。"""
    return {
        "value_id": f"dw.dim_order_status.order_status::{raw_value}",
        "dimension_id": "order_status",
        "dimension_name": "order_status",
        "dimension_business_name": "订单状态",
        "column_id": "dw.dim_order_status.order_status",
        "column_name": "order_status",
        "table_id": "dw.dim_order_status",
        "table_name": "dim_order_status",
        "database_name": "dw",
        "data_type": "VARCHAR(32)",
        "raw_value": raw_value,
        "normalized_value": raw_value,
        "display_name": display_name,
        "aliases": [],
        "description": display_name,
        "value_count": 1,
        "semantic_enabled": True,
        "status": "active",
    }


class FakeEmbeddingClient:
    """为检索词返回一个固定向量。"""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[0.1] * 1024]


class FakeFullTextRepository:
    """返回一条 aliases 精确命中结果。"""

    def search_dimension_values(self, **kwargs) -> list[dict]:
        return [
            {
                "id": "delivered",
                "score": 20.0,
                "matched_queries": ["alias_exact", "full_text"],
                "source": make_source("delivered", "已送达"),
            }
        ]


class FakeQdrantRepository:
    """返回相同候选和一个仅语义相关候选。"""

    def search_points(self, **kwargs) -> list[dict]:
        return [
            {
                "id": "delivered-vector",
                "score": 0.91,
                "payload": make_source("delivered", "已送达"),
            },
            {
                "id": "shipped-vector",
                "score": 0.80,
                "payload": make_source("shipped", "已发货"),
            },
        ]


class DimensionValueSearchServiceTest(unittest.TestCase):
    """验证精确匹配置顶、去重和服务调用。"""

    def test_exact_match_ranks_before_semantic_only_match(self) -> None:
        results = merge_dimension_value_results(
            es_hits=FakeFullTextRepository().search_dimension_values(),
            vector_hits=FakeQdrantRepository().search_points(),
            limit=10,
            rrf_k=60,
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["raw_value"], "delivered")
        self.assertTrue(results[0]["exact_match"])
        self.assertEqual(set(results[0]["match_types"]), {"alias_exact", "full_text", "semantic"})

    def test_service_returns_hybrid_results(self) -> None:
        service = DimensionValueSearchService(
            embedding_client=FakeEmbeddingClient(),
            qdrant_repository=FakeQdrantRepository(),
            es_repository=FakeFullTextRepository(),
        )

        results = service.search("已经收到货")

        self.assertEqual(results[0]["raw_value"], "delivered")
        self.assertEqual(results[0]["column_name"], "order_status")


if __name__ == "__main__":
    unittest.main()
