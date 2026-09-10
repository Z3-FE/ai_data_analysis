"""SQL 生成结果解析测试。"""

import asyncio
import json
import unittest
from types import SimpleNamespace

from langchain_core.runnables import RunnableLambda

from app.agent.nodes.generate_sql import _parse_generation_result, generate_sql


class GenerateSqlResultTest(unittest.TestCase):
    """验证结构化 SQL 输出和旧版纯 SQL 输出都能进入统一格式。"""

    def test_parses_structured_sql_result(self) -> None:
        result = _parse_generation_result(
            """```json
            {
              "sql": "SELECT category_display_name, SUM(price) AS gmv FROM orders",
              "result_columns": [
                {
                  "result_name": "category_display_name",
                  "field_role": "dimension",
                  "display_name": "商品类别",
                  "source_column_id": "dw.dim_category.category_display_name",
                  "source_metric_id": "",
                  "source_fields": [],
                  "filter_conditions": []
                },
                {
                  "result_name": "gmv",
                  "field_role": "metric",
                  "display_name": "销售额",
                  "source_column_id": "",
                  "source_metric_id": "gmv",
                  "source_fields": [],
                  "filter_conditions": []
                }
              ]
            }
            ```"""
        )

        self.assertEqual(
            result["sql"],
            "SELECT category_display_name, SUM(price) AS gmv FROM orders",
        )
        self.assertEqual(len(result["result_columns"]), 2)
        self.assertEqual(
            result["result_columns"][0]["source_column_id"],
            "dw.dim_category.category_display_name",
        )

    def test_keeps_plain_sql_compatible(self) -> None:
        result = _parse_generation_result(
            "```sql\nSELECT SUM(price) AS gmv FROM orders\n```"
        )

        self.assertEqual(result["sql"], "SELECT SUM(price) AS gmv FROM orders")
        self.assertEqual(result["result_columns"], [])

    def test_invalid_column_contract_keeps_embedded_sql(self) -> None:
        result = _parse_generation_result(
            '{"sql": "SELECT SUM(price) AS gmv FROM orders", '
            '"result_columns": [{"result_name": ""}]}'
        )

        self.assertEqual(
            result,
            {
                "sql": "SELECT SUM(price) AS gmv FROM orders",
                "result_columns": [],
            },
        )

    def test_invalid_filter_only_degrades_affected_column(self) -> None:
        result = _parse_generation_result(
            json.dumps(
                {
                    "sql": "SELECT seller_state, SUM(price) AS gmv, "
                    "COUNT(DISTINCT order_id) AS order_count FROM orders",
                    "result_columns": [
                        {
                            "result_name": "seller_state",
                            "field_role": "dimension",
                            "display_name": "卖家州",
                            "source_column_id": "dw.dim_seller.seller_state",
                        },
                        {
                            "result_name": "gmv",
                            "field_role": "metric",
                            "display_name": "销售额",
                            "source_metric_id": "gmv",
                        },
                        {
                            "result_name": "order_count",
                            "field_role": "derived_metric",
                            "display_name": "包含该卖家州商品的去重订单数",
                            "filter_conditions": ["2017-12"],
                        },
                    ],
                },
                ensure_ascii=False,
            )
        )

        self.assertEqual(len(result["result_columns"]), 3)
        self.assertEqual(result["result_columns"][0]["field_role"], "dimension")
        self.assertEqual(result["result_columns"][1]["field_role"], "metric")
        self.assertEqual(
            result["result_columns"][2]["field_role"], "derived_metric"
        )
        self.assertEqual(
            result["result_columns"][2]["display_name"],
            "包含该卖家州商品的去重订单数",
        )
        self.assertEqual(result["result_columns"][2]["filter_conditions"], [])
        self.assertEqual(
            result["result_columns"][2]["display_name_source"],
            "llm_declared",
        )
        self.assertEqual(
            result["result_columns"][2]["lineage_status"], "declared"
        )

    def test_keeps_all_columns_when_one_filter_contract_is_invalid(self) -> None:
        result = _parse_generation_result(
            json.dumps(
                {
                    "sql": "SELECT seller_state, SUM(price) AS gmv, "
                    "COUNT(DISTINCT order_id) AS order_count, "
                    "SUM(price) / COUNT(DISTINCT order_id) AS avg_order_value "
                    "FROM orders GROUP BY seller_state",
                    "result_columns": [
                        {
                            "result_name": "seller_state",
                            "field_role": "dimension",
                            "display_name": "卖家地区",
                        },
                        {
                            "result_name": "gmv",
                            "field_role": "metric",
                            "display_name": "销售额",
                            "source_metric_id": "gmv",
                        },
                        {
                            "result_name": "order_count",
                            "field_role": "derived_metric",
                            "display_name": "订单量",
                            "filter_conditions": ["2017-12"],
                        },
                        {
                            "result_name": "avg_order_value",
                            "field_role": "metric",
                            "display_name": "平均每单销售额",
                            "source_metric_id": "avg_order_value",
                        },
                    ],
                },
                ensure_ascii=False,
            )
        )

        self.assertEqual(
            [column["result_name"] for column in result["result_columns"]],
            ["seller_state", "gmv", "order_count", "avg_order_value"],
        )

    def test_parses_structured_filter_condition(self) -> None:
        result = _parse_generation_result(
            json.dumps(
                {
                    "sql": "SELECT SUM(price) AS filtered_gmv FROM orders",
                    "result_columns": [
                        {
                            "result_name": "filtered_gmv",
                            "field_role": "derived_metric",
                            "display_name": "筛选销售额",
                            "filter_conditions": [
                                {
                                    "column_id": "dw.dim_date.year_month_value",
                                    "operator": "=",
                                    "raw_value": "2017-12",
                                }
                            ],
                        }
                    ],
                },
                ensure_ascii=False,
            )
        )

        self.assertEqual(
            result["result_columns"][0]["filter_conditions"][0],
            {
                "column_id": "dw.dim_date.year_month_value",
                "operator": "=",
                "raw_value": "2017-12",
            },
        )

    def test_normalizes_measure_field_role(self) -> None:
        result = _parse_generation_result(
            '{"sql": "SELECT SUM(price) AS gmv FROM orders", '
            '"result_columns": [{"result_name": "gmv", '
            '"field_role": "measure", "display_name": "销售额"}]}'
        )

        self.assertEqual(result["result_columns"][0]["field_role"], "metric")

    def test_normalizes_null_source_ids_without_losing_contract(self) -> None:
        result = _parse_generation_result(
            json.dumps(
                {
                    "sql": "SELECT seller_state, SUM(price) AS gmv FROM orders",
                    "result_columns": [
                        {
                            "result_name": "seller_state",
                            "field_role": "dimension",
                            "display_name": "卖家州",
                            "source_column_id": "dw.dim_seller.seller_state",
                            "source_metric_id": None,
                        },
                        {
                            "result_name": "gmv",
                            "field_role": "metric",
                            "display_name": "销售额",
                            "source_column_id": None,
                            "source_metric_id": "gmv",
                        },
                    ],
                },
                ensure_ascii=False,
            )
        )

        self.assertEqual(
            result["result_columns"][0]["source_column_id"],
            "dw.dim_seller.seller_state",
        )
        self.assertEqual(result["result_columns"][0]["source_metric_id"], "")
        self.assertEqual(result["result_columns"][0]["lineage_status"], "declared")
        self.assertEqual(result["result_columns"][1]["source_column_id"], "")
        self.assertEqual(result["result_columns"][1]["source_metric_id"], "gmv")


class GenerateSqlNodeTest(unittest.IsolatedAsyncioTestCase):
    """验证 SQL 生成节点的日志事件、空返回和超时分支。"""

    def _runtime(self, llm_client, timeout_seconds=1) -> SimpleNamespace:
        events = []
        runtime = SimpleNamespace(
            context={
                "llm_client": llm_client,
                "llm_timeout_seconds": timeout_seconds,
            },
            stream_writer=events.append,
        )
        runtime.events = events
        return runtime

    async def test_emits_raw_llm_result_event(self) -> None:
        llm = RunnableLambda(
            lambda _: json.dumps(
                {
                    "sql": "SELECT 1",
                    "result_columns": [],
                }
            )
        )
        runtime = self._runtime(llm)

        result = await generate_sql(
            {
                "input_text": "查询测试数据",
                "extra_context": {},
            },
            runtime,
        )

        self.assertEqual(result["sql"], "SELECT 1")
        llm_events = [event for event in runtime.events if event["type"] == "llm_result"]
        self.assertEqual(len(llm_events), 1)
        self.assertIn('\"sql\": \"SELECT 1\"', llm_events[0]["raw_result"])

    async def test_emits_incremental_llm_chunks_before_final_result(self) -> None:
        async def stream_response(_):
            yield '{"sql": '
            yield '"SELECT 1", '
            yield '"result_columns": []}'

        runtime = self._runtime(RunnableLambda(stream_response))

        result = await generate_sql(
            {
                "input_text": "查询测试数据",
                "extra_context": {},
            },
            runtime,
        )

        self.assertEqual(result["sql"], "SELECT 1")
        chunk_events = [
            event for event in runtime.events if event["type"] == "llm_chunk"
        ]
        self.assertEqual(
            [event["chunk"] for event in chunk_events],
            ['{"sql": ', '"SELECT 1", ', '"result_columns": []}'],
        )
        event_types = [event["type"] for event in runtime.events]
        self.assertLess(
            event_types.index("llm_chunk"),
            event_types.index("llm_result"),
        )

    async def test_emits_reasoning_chunks_and_preserves_full_reasoning(self) -> None:
        reasoning_one = "一" * 50
        reasoning_two = "二" * 50

        class FakeAutoLLM:
            async def astream_auto(self, _input):
                yield SimpleNamespace(event_type="reasoning", text=reasoning_one)
                yield SimpleNamespace(event_type="reasoning", text=reasoning_two)
                yield SimpleNamespace(
                    event_type="content",
                    text='{"sql": "SELECT 1", "result_columns": []}',
                )
                yield SimpleNamespace(event_type="metadata", text="")

        runtime = self._runtime(FakeAutoLLM())

        result = await generate_sql(
            {
                "input_text": "查询测试数据",
                "extra_context": {},
            },
            runtime,
        )

        reasoning_events = [
            event
            for event in runtime.events
            if event["type"] == "reasoning_chunk"
        ]
        self.assertEqual(len(reasoning_events), 2)
        self.assertEqual(
            [event["chunk"] for event in reasoning_events],
            [reasoning_one, reasoning_two],
        )
        self.assertEqual(result["sql_reasoning"], reasoning_one + reasoning_two)

    async def test_emits_timeout_event(self) -> None:
        async def slow_response(_):
            await asyncio.sleep(0.05)
            return "{\"sql\": \"SELECT 1\", \"result_columns\": []}"

        runtime = self._runtime(RunnableLambda(slow_response), timeout_seconds=0.001)

        with self.assertRaisesRegex(TimeoutError, "SQL 生成超时"):
            await generate_sql(
                {
                    "input_text": "查询测试数据",
                    "extra_context": {},
                },
                runtime,
            )

        failed_events = [
            event
            for event in runtime.events
            if event["type"] == "generate_sql"
            and event["status"] == "failed"
        ]
        self.assertEqual(len(failed_events), 1)
        self.assertIn("SQL 生成超时", failed_events[0]["error"])


if __name__ == "__main__":
    unittest.main()
