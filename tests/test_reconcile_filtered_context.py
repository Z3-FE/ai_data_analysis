"""过滤上下文补全中的日期关系选择测试。"""

import unittest

from app.agent.nodes.reconcile_filtered_context import (
    _find_table_path,
    _resolve_table_id,
)


def make_relationship(relationship_id: str, source: str, column_name: str) -> dict:
    return {
        "relationship_id": relationship_id,
        "from_table_id": source,
        "from_column_name": column_name,
        "to_table_id": "dw.dim_date",
        "to_column_name": "date_key",
        "relationship_type": "many_to_one",
        "description": relationship_id,
    }


class FindTablePathDatePriorityTest(unittest.TestCase):
    def test_order_defaults_to_purchase_date_despite_shared_date_key(self):
        relationships = [
            make_relationship("approved", "dw.fact_order", "approved_date_key"),
            make_relationship("purchase", "dw.fact_order", "purchase_date_key"),
        ]

        path = _find_table_path(
            "dw.fact_order",
            "dw.dim_date",
            relationships,
            {"dw.dim_date.date_key", "dw.fact_order.approved_date_key"},
            "帮我看一下2017年的延迟率",
        )

        self.assertEqual(path[0]["relationship_id"], "purchase")

    def test_review_defaults_to_creation_date(self):
        relationships = [
            make_relationship(
                "review_answer", "dw.fact_review", "review_answer_date_key"
            ),
            make_relationship(
                "review_creation", "dw.fact_review", "review_creation_date_key"
            ),
        ]

        path = _find_table_path(
            "dw.fact_review",
            "dw.dim_date",
            relationships,
            {"dw.dim_date.date_key"},
            "帮我看一下2017年的平均评价分数",
        )

        self.assertEqual(path[0]["relationship_id"], "review_creation")

    def test_explicit_review_answer_date_overrides_default(self):
        relationships = [
            make_relationship(
                "review_answer", "dw.fact_review", "review_answer_date_key"
            ),
            make_relationship(
                "review_creation", "dw.fact_review", "review_creation_date_key"
            ),
        ]

        path = _find_table_path(
            "dw.fact_review",
            "dw.dim_date",
            relationships,
            set(),
            "按评价回复日期看2017年的平均评价分数",
        )

        self.assertEqual(path[0]["relationship_id"], "review_answer")


class ResolveTableIdTest(unittest.TestCase):
    """验证 LLM 省略 schema 时只进行唯一候选解析。"""

    def test_resolves_unique_short_table_name(self):
        tables = {"dw.dim_category": {"table_id": "dw.dim_category"}}

        self.assertEqual(
            _resolve_table_id("dim_category", tables),
            "dw.dim_category",
        )

    def test_rejects_ambiguous_short_table_name(self):
        tables = {
            "dw.dim_category": {"table_id": "dw.dim_category"},
            "ods.dim_category": {"table_id": "ods.dim_category"},
        }

        with self.assertRaisesRegex(ValueError, "不唯一"):
            _resolve_table_id("dim_category", tables)


if __name__ == "__main__":
    unittest.main()
