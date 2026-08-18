"""四路召回信息合并节点测试。"""

import asyncio
import unittest

from app.agent.nodes.merge_retrieved_info import merge_retrieved_info
from app.entities.agent.agent_merge_context import MetricDimensionInfo, RelationshipInfo
from app.entities.meta.meta_columns import MetaColumns
from app.entities.meta.meta_dimensions import MetaDimensions
from app.entities.meta.meta_tables import MetaTables


def make_table(table_id: str, table_name: str, table_type: str) -> MetaTables:
    return MetaTables(
        table_id=table_id,
        data_source_id="olist_dw",
        database_name="dw",
        table_name=table_name,
        table_type=table_type,
        business_name=table_name,
        grain="一行一条记录",
        description=table_name,
        aliases=[],
        status="active",
    )


def make_column(column_id: str, table_id: str, column_name: str) -> MetaColumns:
    return MetaColumns(
        column_id=column_id,
        table_id=table_id,
        column_name=column_name,
        business_name=column_name,
        data_type="VARCHAR(32)",
        semantic_role="dimension",
        is_queryable=True,
        is_aggregatable=False,
        description=column_name,
        aliases=[],
        status="active",
    )


class FakeMetaCatalogRepository:
    """返回合并节点需要的固定 Meta 元数据。"""

    async def get_columns_by_ids(self, column_ids):
        return [
            make_column(
                "dw.dim_payment_type.payment_type",
                "dw.dim_payment_type",
                "payment_type",
            )
        ]

    async def get_tables_by_ids(self, table_ids):
        tables = {
            "dw.dim_payment_type": make_table(
                "dw.dim_payment_type", "dim_payment_type", "dimension"
            ),
            "dw.fact_payment": make_table(
                "dw.fact_payment", "fact_payment", "fact"
            ),
        }
        return [tables[table_id] for table_id in table_ids]

    async def get_queryable_columns_by_table_ids(self, table_ids):
        return [
            make_column(
                "dw.dim_payment_type.payment_type",
                "dw.dim_payment_type",
                "payment_type",
            ),
            make_column(
                "dw.fact_payment.payment_value",
                "dw.fact_payment",
                "payment_value",
            ),
        ]

    async def get_relationships_by_table_ids(self, table_ids):
        return [
            RelationshipInfo(
                relationship_id="fact_payment_to_dim_payment_type",
                from_table_id="dw.fact_payment",
                from_column_name="payment_type_key",
                to_table_id="dw.dim_payment_type",
                to_column_name="payment_type_key",
                relationship_type="many_to_one",
                description="支付事实关联支付方式维度",
            )
        ]

    async def get_dimensions_by_column_ids(self, column_ids):
        return [
            MetaDimensions(
                dimension_id="payment_type",
                dimension_name="payment_type",
                business_name="支付方式",
                table_id="dw.dim_payment_type",
                column_name="payment_type",
                description="按支付方式分析",
            )
        ]

    async def get_metric_dimension_infos(self, metric_ids, dimension_ids):
        return [
            MetricDimensionInfo(
                metric_id="payment_amount",
                dimension_id="payment_type",
                compatibility_note="允许按支付方式分析",
            )
        ]


class FakeRuntime:
    """提供 Context 和 SSE writer 的最小 Runtime。"""

    def __init__(self):
        self.context = {"meta_catalog_repository": FakeMetaCatalogRepository()}
        self.events = []
        self.stream_writer = self.events.append


class MissingQueryableColumnRepository(FakeMetaCatalogRepository):
    """模拟召回字段未登记在 Meta 可查询字段目录中的异常数据。"""

    async def get_queryable_columns_by_table_ids(self, table_ids):
        return []


class MissingQueryableColumnRuntime(FakeRuntime):
    def __init__(self):
        self.context = {
            "meta_catalog_repository": MissingQueryableColumnRepository()
        }
        self.events = []
        self.stream_writer = self.events.append


class MergeRetrievedInfoTest(unittest.TestCase):
    def test_merge_completes_tables_values_relationships_and_compatibility(self):
        state = {
            "table_candidates": [],
            "column_candidates": [],
            "metrics_candidates": [
                {
                    "payload": {
                        "metric_id": "payment_amount",
                        "metric_name": "payment_amount",
                        "business_name": "支付金额",
                        "base_table_id": "dw.fact_payment",
                        "expression_sql": "SUM(payment_value)",
                        "aggregation_type": "sum",
                        "unit": "currency",
                        "description": "支付记录金额之和",
                        "aliases": [],
                        "status": "active",
                    }
                }
            ],
            "dimension_value_candidates": [
                {
                    "value_id": "payment_type::credit_card",
                    "dimension_id": "payment_type",
                    "column_id": "dw.dim_payment_type.payment_type",
                    "table_id": "dw.dim_payment_type",
                    "raw_value": "credit_card",
                    "normalized_value": "credit card",
                    "display_name": "信用卡支付",
                    "aliases": ["刷卡支付"],
                    "description": "使用信用卡完成支付",
                    "exact_match": True,
                    "exact_priority": 2,
                    "match_types": {
                        "display_name_exact": "中文展示名称精确匹配",
                        "semantic": "Qdrant 向量语义匹配",
                    },
                    "matched_terms": ["信用卡支付"],
                    "rrf_score": 0.03,
                    "es_score": 18.0,
                    "vector_score": 0.9,
                }
            ],
        }
        runtime = FakeRuntime()

        result = asyncio.run(merge_retrieved_info(state, runtime))

        self.assertEqual(len(result["table_infos"]), 2)
        payment_table = next(
            table
            for table in result["table_infos"]
            if table["table_id"] == "dw.dim_payment_type"
        )
        payment_column = payment_table["columns"][0]
        self.assertEqual(payment_column["matched_values"][0]["raw_value"], "credit_card")
        self.assertEqual(
            payment_table["matched_sources"],
            {
                "dimension_value_completion": "根据维度值召回结果从 Meta MySQL 补齐的所属字段",
                "dimension_value_recall": "维度值召回命中，维度值所属字段和表因此成为候选",
            },
        )
        self.assertEqual(
            payment_column["matched_sources"],
            {
                "dimension_value_completion": "根据维度值召回结果从 Meta MySQL 补齐的所属字段",
                "dimension_value_recall": "维度值召回命中，维度值所属字段和表因此成为候选",
            },
        )
        self.assertEqual(result["metric_infos"][0]["metric_id"], "payment_amount")
        self.assertEqual(result["dimension_infos"][0]["dimension_id"], "payment_type")
        self.assertEqual(
            result["metric_infos"][0]["matched_sources"],
            {"metric_recall": "指标语义召回命中，指标基础表因此成为候选表"},
        )
        self.assertEqual(len(result["relationship_infos"]), 1)
        self.assertEqual(len(result["metric_dimension_infos"]), 1)

    def test_merge_rejects_recalled_column_outside_queryable_catalog(self):
        state = {
            "table_candidates": [],
            "column_candidates": [
                {
                    "payload": {
                        **make_column(
                            "dw.fact_payment.payment_value",
                            "dw.fact_payment",
                            "payment_value",
                        ).__dict__
                    }
                }
            ],
            "metrics_candidates": [],
            "dimension_value_candidates": [],
        }

        with self.assertRaisesRegex(ValueError, "未能挂载到候选表"):
            asyncio.run(
                merge_retrieved_info(state, MissingQueryableColumnRuntime())
            )

if __name__ == "__main__":
    unittest.main()
