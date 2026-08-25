"""SQL 执行后结果增强节点测试。"""

import unittest
from types import SimpleNamespace

from app.agent.nodes.enrich_query_result import enrich_query_result


class FakeMetaCatalogRepository:
    """返回维度值展示名称的最小 Meta 仓储。"""

    def __init__(self) -> None:
        self.requests = []

    async def get_dimension_values_by_raw_values(self, value_keys):
        self.requests = list(value_keys)
        return [
            {
                "value_id": "category::bed_bath_table",
                "dimension_id": "product_category",
                "column_id": "dw.dim_category.category_display_name",
                "raw_value": "bed_bath_table",
                "normalized_value": "bed_bath_table",
                "display_name": "床浴桌用品",
                "dimension_business_name": "商品类别",
                "column_name": "category_display_name",
            }
        ]

    async def get_dimension_value_column_ids(self, column_ids):
        return [
            column_id
            for column_id in column_ids
            if column_id == "dw.dim_category.category_display_name"
        ]


class EnrichQueryResultTest(unittest.IsolatedAsyncioTestCase):
    """验证映射只增加展示副本，不改变 SQL 原始结果。"""

    async def test_does_not_bind_derived_order_count_to_original_metric(self) -> None:
        """同名动态字段不能被误认成 fact_order.order_count。"""
        repository = FakeMetaCatalogRepository()
        runtime = SimpleNamespace(
            context={"meta_catalog_repository": repository},
            stream_writer=lambda _: None,
        )
        state = {
            "sql_result": [{"seller_state": "SP", "order_count": 4031}],
            "table_infos": [
                {
                    "table_id": "dw.fact_order",
                    "columns": [
                        {
                            "column_id": "dw.fact_order.order_count",
                            "column_name": "order_count",
                            "business_name": "订单数",
                            "semantic_role": "measure",
                        }
                    ],
                }
            ],
            "metric_infos": [
                {
                    "metric_id": "order_count",
                    "metric_name": "order_count",
                    "business_name": "订单量",
                    "unit": "order",
                }
            ],
            "result_columns": [
                {
                    "result_name": "order_count",
                    "field_role": "derived_metric",
                    "display_name": "包含该卖家州商品的去重订单数",
                    "source_metric_id": "",
                    "source_fields": ["order_id"],
                    "filter_conditions": [],
                }
            ],
        }

        result = await enrich_query_result(state, runtime)

        order_count_column = result["result_columns"][1]
        self.assertEqual(order_count_column["field_role"], "derived_metric")
        self.assertEqual(order_count_column["display_name"], "包含该卖家州商品的去重订单数")
        self.assertEqual(order_count_column["source_column_id"], "")
        self.assertEqual(order_count_column["source_metric_id"], "")

    async def test_does_not_guess_ambiguous_metric_without_declaration(self) -> None:
        """没有声明时，同名物理字段和指标也不能自动绑定。"""
        runtime = SimpleNamespace(
            context={"meta_catalog_repository": FakeMetaCatalogRepository()},
            stream_writer=lambda _: None,
        )
        result = await enrich_query_result(
            {
                "sql_result": [{"order_count": 10}],
                "table_infos": [
                    {
                        "table_id": "dw.fact_order",
                        "columns": [
                            {
                                "column_id": "dw.fact_order.order_count",
                                "column_name": "order_count",
                                "business_name": "订单数",
                                "semantic_role": "measure",
                            }
                        ],
                    }
                ],
                "metric_infos": [
                    {
                        "metric_id": "order_count",
                        "metric_name": "order_count",
                        "business_name": "订单量",
                    }
                ],
            },
            runtime,
        )

        order_count_column = result["result_columns"][0]
        self.assertEqual(order_count_column["source_column_id"], "")
        self.assertEqual(order_count_column["source_metric_id"], "")
        self.assertEqual(order_count_column["field_role"], "unknown")

    async def test_maps_dimension_value_and_preserves_raw_rows(self) -> None:
        repository = FakeMetaCatalogRepository()
        rows = [
            {"category_display_name": "bed_bath_table", "gmv": 1000},
            {"category_display_name": "unknown_category", "gmv": 800},
        ]
        state = {
            "sql_result": rows,
            "table_infos": [
                {
                    "table_id": "dw.dim_category",
                    "table_name": "dim_category",
                    "columns": [
                        {
                            "column_id": "dw.dim_category.category_display_name",
                            "column_name": "category_display_name",
                            "business_name": "商品类别名称",
                            "semantic_role": "dimension",
                        }
                    ],
                }
            ],
            "metric_infos": [
                {
                    "metric_id": "gmv",
                    "metric_name": "gmv",
                    "business_name": "销售额",
                    "unit": "currency",
                }
            ],
            "dimension_infos": [
                {
                    "dimension_id": "product_category",
                    "business_name": "商品类别",
                    "table_id": "dw.dim_category",
                    "column_name": "category_display_name",
                }
            ],
        }
        runtime = SimpleNamespace(
            context={"meta_catalog_repository": repository},
            stream_writer=lambda _: None,
        )

        result = await enrich_query_result(state, runtime)

        self.assertEqual(rows[0]["category_display_name"], "bed_bath_table")
        self.assertEqual(
            result["display_sql_result"][0]["category_display_name"],
            "床浴桌用品",
        )
        self.assertEqual(
            result["display_sql_result"][1]["category_display_name"],
            "unknown_category",
        )
        self.assertEqual(
            repository.requests,
            [
                (
                    "dw.dim_category.category_display_name",
                    "bed_bath_table",
                ),
                (
                    "dw.dim_category.category_display_name",
                    "unknown_category",
                ),
            ],
        )
        mapping = result["dimension_value_mappings"]
        self.assertEqual(mapping[0]["status"], "mapped")
        self.assertEqual(mapping[1]["status"], "unmapped")
        self.assertTrue(result["mapping_limitations"])
        category_column = result["result_columns"][0]
        self.assertEqual(category_column["field_role"], "dimension")
        self.assertEqual(category_column["dimension_business_name"], "商品类别")
        metric_column = result["result_columns"][1]
        self.assertEqual(metric_column["field_role"], "metric")
        self.assertEqual(metric_column["display_name"], "销售额")

    async def test_does_not_query_meta_for_numeric_only_result(self) -> None:
        repository = FakeMetaCatalogRepository()
        runtime = SimpleNamespace(
            context={"meta_catalog_repository": repository},
            stream_writer=lambda _: None,
        )

        result = await enrich_query_result(
            {"sql_result": [{"gmv": 1000}]}, runtime
        )

        self.assertEqual(repository.requests, [])
        self.assertEqual(result["dimension_value_mappings"], [])
        self.assertEqual(result["display_sql_result"], [{"gmv": 1000}])

    async def test_keeps_unindexed_dimension_without_mapping_limitation(self) -> None:
        repository = FakeMetaCatalogRepository()
        runtime = SimpleNamespace(
            context={"meta_catalog_repository": repository},
            stream_writer=lambda _: None,
        )
        state = {
            "sql_result": [{"year_month_value": "2017-12"}],
            "table_infos": [
                {
                    "table_id": "dw.dim_date",
                    "columns": [
                        {
                            "column_id": "dw.dim_date.year_month_value",
                            "column_name": "year_month_value",
                            "business_name": "年月",
                            "semantic_role": "dimension",
                        }
                    ],
                }
            ],
            "dimension_infos": [
                {
                    "dimension_id": "purchase_month",
                    "business_name": "下单月份",
                    "table_id": "dw.dim_date",
                    "column_name": "year_month_value",
                }
            ],
        }

        result = await enrich_query_result(state, runtime)

        self.assertEqual(
            result["display_sql_result"], [{"year_month_value": "2017-12"}]
        )
        self.assertEqual(repository.requests, [])
        self.assertEqual(result["dimension_value_mappings"], [])
        self.assertEqual(result["mapping_limitations"], [])


if __name__ == "__main__":
    unittest.main()
