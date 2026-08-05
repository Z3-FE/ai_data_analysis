"""维度值两级混合检索测试。"""

import unittest

from app.services.semantic.dimension_value_search_service import (
    merge_all_term_results,
    merge_term_results,
)


def make_source(raw_value: str, display_name: str) -> dict:
    """创建一条完整的维度值业务数据。"""
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


def make_es_hit(raw_value: str, display_name: str) -> dict:
    """创建一条 ES 精确匹配结果。"""
    return {
        "id": raw_value,
        "score": 20.0,
        "matched_queries": ["alias_exact", "full_text"],
        "source": make_source(raw_value, display_name),
    }


def make_vector_hit(raw_value: str, display_name: str, score: float = 0.9) -> dict:
    """创建一条 Qdrant 语义匹配结果。"""
    return {
        "id": f"{raw_value}-vector",
        "score": score,
        "payload": {
            **make_source(raw_value, display_name),
            "vector_type": "alias",
            "point_key": f"{raw_value}::alias::0",
            "text": f"订单状态的常见说法：{display_name}",
        },
    }


class DimensionValueSearchServiceTest(unittest.TestCase):
    """验证关键词内融合和关键词间全局融合。"""

    def test_term_merge_combines_es_and_vector_evidence(self) -> None:
        """同一关键词的 ES、Qdrant 相同候选应合并为一条。"""
        results = merge_term_results(
            recall_term="已经收到货",
            es_hits=[make_es_hit("delivered", "已送达")],
            vector_hits=[
                make_vector_hit("delivered", "已送达", 0.91),
                make_vector_hit("shipped", "已发货", 0.80),
            ],
            limit=10,
            rrf_k=60,
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].source["raw_value"], "delivered")
        self.assertEqual(results[0].matched_terms, {"已经收到货"})
        self.assertEqual(
            results[0].match_types,
            {"alias_exact", "full_text", "semantic"},
        )

    def test_global_merge_preserves_all_matching_terms(self) -> None:
        """同一业务值被多个关键词命中时应汇总 matched_terms。"""
        first_term = merge_term_results(
            recall_term="信用卡支付",
            es_hits=[make_es_hit("credit_card", "信用卡支付")],
            vector_hits=[make_vector_hit("credit_card", "信用卡支付")],
            limit=10,
            rrf_k=60,
        )
        second_term = merge_term_results(
            recall_term="刷卡支付",
            es_hits=[],
            vector_hits=[make_vector_hit("credit_card", "信用卡支付")],
            limit=10,
            rrf_k=60,
        )

        results = merge_all_term_results(
            term_results=[first_term, second_term],
            total_limit=50,
            rrf_k=60,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(
            set(results[0]["matched_terms"]),
            {"信用卡支付", "刷卡支付"},
        )
        self.assertNotIn("point_key", results[0])
        self.assertNotIn("vector_type", results[0])
        self.assertNotIn("text", results[0])

    def test_multiple_vector_points_only_contribute_one_rrf_rank(self) -> None:
        """同一业务值的多个向量点不能重复增加当前关键词的 RRF 分数。"""
        results = merge_term_results(
            recall_term="订单完成",
            es_hits=[],
            vector_hits=[
                make_vector_hit("delivered", "已送达", 0.91),
                make_vector_hit("delivered", "已送达", 0.88),
            ],
            limit=10,
            rrf_k=60,
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].rrf_score, 1 / 61)
        self.assertEqual(results[0].vector_score, 0.91)
        self.assertNotIn("point_key", results[0].source)
        self.assertNotIn("vector_type", results[0].source)
        self.assertNotIn("text", results[0].source)

    def test_limits_are_maximums_not_required_counts(self) -> None:
        """上限为 10 时，只有一条可信结果就只返回一条。"""
        results = merge_term_results(
            recall_term="已送达",
            es_hits=[make_es_hit("delivered", "已送达")],
            vector_hits=[],
            limit=10,
            rrf_k=60,
        )

        self.assertEqual(len(results), 1)


if __name__ == "__main__":
    unittest.main()
