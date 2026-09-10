"""SQL 生成上下文整理测试。"""

import asyncio
import unittest
from types import SimpleNamespace

from app.agent.nodes.add_extra_context import add_extra_context


class AddExtraContextTest(unittest.TestCase):
    """验证表粒度、关系用途和指标维度矩阵都进入 SQL 上下文。"""

    def test_builds_grain_and_compatibility_context(self) -> None:
        events = []
        runtime = SimpleNamespace(stream_writer=events.append)
        state = {
            "table_infos": [
                {
                    "table_id": "dw.fact_order",
                    "table_type": "fact",
                    "business_name": "订单事实",
                    "grain": "一个订单一行",
                    "description": "订单级事实",
                    "columns": [],
                },
                {
                    "table_id": "dw.dim_seller",
                    "table_type": "dimension",
                    "business_name": "卖家维度",
                    "grain": "一个 seller_id 一行",
                    "description": "卖家信息",
                    "columns": [
                        {
                            "column_id": "dw.dim_seller.seller_state",
                            "column_name": "seller_state",
                            "business_name": "卖家州",
                            "semantic_role": "dimension",
                            "data_type": "VARCHAR(8)",
                            "is_aggregatable": False,
                            "description": "卖家所在州",
                            "matched_values": [],
                        }
                    ],
                },
            ],
            "metric_infos": [
                {
                    "metric_id": "order_count",
                    "metric_name": "order_count",
                    "business_name": "订单量",
                    "base_table_id": "dw.fact_order",
                    "expression_sql": "SUM(order_count)",
                    "aggregation_type": "sum",
                    "description": "每行一个订单",
                    "unit": "order",
                }
            ],
            "dimension_infos": [
                {
                    "dimension_id": "seller_state",
                    "business_name": "卖家州",
                    "table_id": "dw.dim_seller",
                    "column_name": "seller_state",
                }
            ],
            "relationship_infos": [
                {
                    "relationship_id": "fact_order_to_fact_order_item_by_order",
                    "from_table_id": "dw.fact_order",
                    "from_column_name": "order_id",
                    "to_table_id": "dw.fact_order_item",
                    "to_column_name": "order_id",
                    "relationship_type": "filter_exists",
                    "description": "按明细条件筛选订单",
                }
            ],
            # 没有登记 order_count 与 seller_state 的兼容关系，
            # 因此这里只能作为模型参考，不应在程序中直接抛错。
            "metric_dimension_infos": [],
        }

        result = asyncio.run(add_extra_context(state, runtime))
        context = result["extra_context"]

        self.assertEqual(context["table_context"][0]["grain"], "一个订单一行")
        self.assertEqual(
            context["metric_context"][0]["base_table_grain"],
            "一个订单一行",
        )
        self.assertEqual(
            context["relationship_context"][0]["from_table_grain"],
            "一个订单一行",
        )
        self.assertIn(
            "EXISTS", context["relationship_context"][0]["sql_usage"]
        )

        compatibility = context["metric_dimension_context"][0]
        self.assertFalse(compatibility["supported"])
        self.assertIn("未登记", compatibility["compatibility_note"])
        self.assertEqual(
            [event["type"] for event in events],
            ["progress", "extra_context"],
        )


if __name__ == "__main__":
    unittest.main()
