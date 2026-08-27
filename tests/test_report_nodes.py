"""报告规划和报告渲染节点测试。"""

import json
import unittest
from types import SimpleNamespace

from app.agent.nodes.generate_report_plan import generate_report_plan
from app.agent.nodes.render_report import render_report


class StreamingPlanLlm:
    """按多个正文片段返回报告规划 JSON。"""

    def __init__(self, payload: dict) -> None:
        self.payload = json.dumps(payload, ensure_ascii=False)
        self.prompts: list[str] = []

    async def astream(self, prompt: str):
        self.prompts.append(prompt)
        midpoint = max(len(self.payload) // 2, 1)
        yield self.payload[:midpoint]
        yield self.payload[midpoint:]


def _runtime(llm: object | None, events: list[dict]) -> SimpleNamespace:
    """构造报告节点需要的最小 Runtime。"""
    return SimpleNamespace(
        context={"llm_client": llm, "llm_timeout_seconds": 30},
        stream_writer=events.append,
    )


def _single_query_state(row_count: int = 20) -> dict:
    """返回带字段契约和展示映射的单次查询状态。"""
    rows = [
        {"seller_state": f"state_{index}", "gmv": index * 100}
        for index in range(row_count)
    ]
    return {
        "input_text": "查询各卖家地区销售额",
        "execution_mode": "single_query",
        "sql": "SELECT seller_state, SUM(price) AS gmv FROM fact GROUP BY seller_state",
        "sql_result": rows,
        "display_sql_result": [
            {**row, "seller_state": f"地区 {index}"}
            for index, row in enumerate(rows)
        ],
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
            },
        ],
    }


def _report_plan() -> dict:
    """返回同时包含文字、表格和图表的报告规划。"""
    return {
        "title": "卖家地区销售报告",
        "summary": "SP 地区销售额最高。",
        "sections": [
            {
                "title": "核心结果",
                "layout": {"type": "grid", "columns": 2},
                "components": [
                    {
                        "component_type": "text",
                        "title": "结论",
                        "content": "SP 地区销售额最高。",
                        "span": 2,
                    },
                    {
                        "component_type": "table",
                        "title": "地区明细",
                        "data_ref": {"source_task_id": "single_query"},
                        "requested_columns": ["seller_state", "gmv"],
                    },
                    {
                        "component_type": "chart",
                        "chart_type": "bar",
                        "title": "地区销售额",
                        "data_ref": {
                            "source_task_id": "single_query",
                            "source": "rows",
                            "dimension_field": "seller_state",
                            "metric_fields": ["gmv"],
                        },
                        "presentation": {
                            "orientation": "horizontal",
                            "sort": "desc",
                            "top_n": 10,
                        },
                    },
                ],
            }
        ],
        "limitations": [],
    }


class ReportNodesTest(unittest.IsolatedAsyncioTestCase):
    """验证规划只引用数据，渲染节点再绑定真实结果。"""

    async def test_generate_report_plan_uses_controlled_preview(self) -> None:
        llm = StreamingPlanLlm(_report_plan())
        events: list[dict] = []

        result = await generate_report_plan(
            _single_query_state(),
            _runtime(llm, events),
        )

        self.assertEqual(result["report_plan_status"], "success")
        self.assertEqual(
            result["report_plan"]["sections"][0]["layout"]["type"],
            "grid",
        )
        self.assertEqual(
            [event["type"] for event in events],
            [
                "progress",
                "llm_chunk",
                "llm_chunk",
                "reasoning_result",
                "llm_result",
                "report_plan_result",
                "progress",
            ],
        )
        self.assertIn("地区 11", llm.prompts[0])
        self.assertNotIn("state_12", llm.prompts[0])
        self.assertNotIn('\"rows\":', llm.prompts[0])

    async def test_render_report_binds_real_display_rows(self) -> None:
        state = _single_query_state()
        state["report_plan"] = _report_plan()
        events: list[dict] = []

        result = await render_report(state, _runtime(None, events))

        report = result["rendered_report"]
        components = report["sections"][0]["components"]
        table = components[1]
        chart = components[2]
        self.assertEqual(report["status"], "success")
        self.assertEqual(report["sections"][0]["layout"]["columns"], 2)
        self.assertEqual(table["data"][0]["seller_state"], "地区 0")
        self.assertEqual(table["row_count"], 20)
        self.assertEqual(chart["columns"][0]["display_name"], "卖家地区")
        self.assertEqual(chart["presentation"]["top_n"], 10)
        self.assertEqual(
            [event["type"] for event in events],
            ["progress", "rendered_report", "progress"],
        )

    async def test_render_report_keeps_failed_component(self) -> None:
        state = _single_query_state()
        plan = _report_plan()
        plan["sections"][0]["components"].append(
            {
                "component_type": "table",
                "title": "不存在的字段",
                "data_ref": {"source_task_id": "single_query"},
                "requested_columns": ["missing_column"],
            }
        )
        state["report_plan"] = plan

        result = await render_report(state, _runtime(None, []))

        report = result["rendered_report"]
        failed = report["sections"][0]["components"][-1]
        self.assertEqual(report["status"], "partial")
        self.assertEqual(failed["binding_status"], "failed")
        self.assertIn("missing_column", failed["binding_error"])
        self.assertTrue(report["limitations"])

    async def test_render_report_binds_calculation_result_kpi(self) -> None:
        state = {
            "input_text": "分析销售额下降月份",
            "execution_mode": "analysis",
            "analysis_evidence": {"status": "success"},
            "analysis_task_results": [
                {
                    "task_id": "monthly_sales",
                    "status": "success",
                    "rows": [{"month": "2017-12", "gmv": 100}],
                    "calculation_result": {"decrease_amount": 20},
                }
            ],
            "report_plan": {
                "title": "销售额下降报告",
                "summary": "12 月销售额下降。",
                "sections": [
                    {
                        "title": "核心指标",
                        "components": [
                            {
                                "component_type": "kpi",
                                "title": "下降金额",
                                "data_ref": {
                                    "source_task_id": "monthly_sales",
                                    "source": "calculation_result",
                                },
                                "value_field": "decrease_amount",
                            }
                        ],
                    }
                ],
            },
        }

        result = await render_report(state, _runtime(None, []))

        component = result["rendered_report"]["sections"][0]["components"][0]
        self.assertEqual(component["binding_status"], "bound")
        self.assertEqual(component["value"], 20)

    async def test_render_report_binds_single_row_kpi_from_rows(self) -> None:
        state = _single_query_state(row_count=1)
        state["report_plan"] = {
            "title": "单值查询报告",
            "summary": "查询返回一个销售额指标。",
            "sections": [
                {
                    "title": "核心指标",
                    "components": [
                        {
                            "component_type": "kpi",
                            "title": "销售额",
                            "data_ref": {
                                "source_task_id": "single_query",
                                "source": "rows",
                            },
                            "value_field": "gmv",
                        }
                    ],
                }
            ],
            "limitations": [],
        }

        result = await render_report(state, _runtime(None, []))

        component = result["rendered_report"]["sections"][0]["components"][0]
        self.assertEqual(component["binding_status"], "bound")
        self.assertEqual(component["value"], 0)

    async def test_render_report_does_not_collapse_multi_row_kpi(self) -> None:
        state = _single_query_state(row_count=2)
        state["report_plan"] = {
            "title": "多行查询报告",
            "summary": "查询返回多个地区。",
            "sections": [
                {
                    "title": "核心指标",
                    "components": [
                        {
                            "component_type": "kpi",
                            "title": "销售额",
                            "data_ref": {
                                "source_task_id": "single_query",
                                "source": "rows",
                            },
                            "value_field": "gmv",
                        }
                    ],
                }
            ],
            "limitations": [],
        }

        result = await render_report(state, _runtime(None, []))

        component = result["rendered_report"]["sections"][0]["components"][0]
        self.assertEqual(component["binding_status"], "failed")
        self.assertIn("需要单行结果", component["binding_error"])

    async def test_render_report_resolves_nested_calculation_field(self) -> None:
        state = {
            "input_text": "分析销售额下降月份",
            "execution_mode": "analysis",
            "analysis_evidence": {"status": "success"},
            "analysis_task_results": [
                {
                    "task_id": "monthly_sales",
                    "status": "success",
                    "calculation_result": {
                        "top_decline": {
                            "target_period": "2017-12",
                            "decrease_amount": 20,
                        }
                    },
                }
            ],
            "report_plan": {
                "title": "销售额下降报告",
                "summary": "12 月销售额下降。",
                "sections": [
                    {
                        "title": "核心指标",
                        "components": [
                            {
                                "component_type": "kpi",
                                "title": "下降月份",
                                "data_ref": {
                                    "source_task_id": "monthly_sales",
                                    "source": "calculation_result",
                                    "path": "top_decline",
                                },
                                "value_field": "target_period",
                            }
                        ],
                    }
                ],
            },
        }

        result = await render_report(state, _runtime(None, []))

        component = result["rendered_report"]["sections"][0]["components"][0]
        self.assertEqual(component["binding_status"], "bound")
        self.assertEqual(component["value"], "2017-12")

    async def test_render_report_falls_back_when_path_is_wrong(self) -> None:
        state = {
            "input_text": "分析销售额下降金额",
            "execution_mode": "analysis",
            "analysis_evidence": {"status": "success"},
            "analysis_task_results": [
                {
                    "task_id": "monthly_sales",
                    "status": "success",
                    "calculation_result": {"decrease_amount": 20},
                }
            ],
            "report_plan": {
                "title": "销售额下降报告",
                "summary": "销售额下降。",
                "sections": [
                    {
                        "title": "核心指标",
                        "components": [
                            {
                                "component_type": "kpi",
                                "title": "下降金额",
                                "data_ref": {
                                    "source_task_id": "monthly_sales",
                                    "source": "calculation_result",
                                    "path": "wrong_path",
                                },
                                "value_field": "decrease_amount",
                            }
                        ],
                    }
                ],
            },
        }

        result = await render_report(state, _runtime(None, []))

        component = result["rendered_report"]["sections"][0]["components"][0]
        self.assertEqual(component["binding_status"], "bound")
        self.assertEqual(component["value"], 20)

    async def test_render_report_rejects_ambiguous_nested_calculation_field(self) -> None:
        state = {
            "input_text": "分析销售额下降金额",
            "execution_mode": "analysis",
            "analysis_evidence": {"status": "success"},
            "analysis_task_results": [
                {
                    "task_id": "monthly_sales",
                    "status": "success",
                    "calculation_result": {
                        "category": {"decrease_amount": 20},
                        "region": {"decrease_amount": 30},
                    },
                }
            ],
            "report_plan": {
                "title": "销售额下降报告",
                "summary": "销售额下降。",
                "sections": [
                    {
                        "title": "核心指标",
                        "components": [
                            {
                                "component_type": "kpi",
                                "title": "下降金额",
                                "data_ref": {
                                    "source_task_id": "monthly_sales",
                                    "source": "calculation_result",
                                },
                                "value_field": "decrease_amount",
                            }
                        ],
                    }
                ],
            },
        }

        result = await render_report(state, _runtime(None, []))

        component = result["rendered_report"]["sections"][0]["components"][0]
        self.assertEqual(component["binding_status"], "failed")
        self.assertIn("category.decrease_amount", component["binding_error"])
        self.assertIn("region.decrease_amount", component["binding_error"])

    async def test_render_report_truncates_large_rows(self) -> None:
        state = _single_query_state(row_count=201)
        state["report_plan"] = _report_plan()

        result = await render_report(state, _runtime(None, []))

        table = result["rendered_report"]["sections"][0]["components"][1]
        self.assertEqual(table["row_count"], 201)
        self.assertEqual(len(table["data"]), 200)
        self.assertTrue(table["truncated"])
        self.assertTrue(result["rendered_report"]["limitations"])


if __name__ == "__main__":
    unittest.main()
