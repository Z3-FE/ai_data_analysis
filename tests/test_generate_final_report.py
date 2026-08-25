"""最终报告节点测试。"""

import json
import unittest
from types import SimpleNamespace

from app.agent.nodes.generate_final_report import generate_final_report


class StreamingReportLlm:
    """记录提示词，并按多个片段模拟最终报告模型的流式输出。"""

    def __init__(self, payload: dict) -> None:
        self.payload = json.dumps(payload, ensure_ascii=False)
        self.prompts: list[str] = []

    async def astream(self, prompt: str):
        self.prompts.append(prompt)
        midpoint = max(len(self.payload) // 2, 1)
        yield self.payload[:midpoint]
        yield self.payload[midpoint:]


def _runtime(llm: StreamingReportLlm, events: list[dict]) -> SimpleNamespace:
    """构造最终报告节点需要的最小 Runtime。"""
    return SimpleNamespace(
        context={"llm_client": llm, "llm_timeout_seconds": 30},
        stream_writer=events.append,
    )


class GenerateFinalReportTest(unittest.IsolatedAsyncioTestCase):
    """验证最终报告的模型编排、真实数据绑定和流式事件。"""

    async def test_binds_real_display_rows_and_streams_report(self) -> None:
        llm = StreamingReportLlm(
            {
                "status": "success",
                "title": "卖家地区销售报告",
                "summary": "SP 地区销售额最高。",
                "sections": [
                    {
                        "title": "核心结果",
                        "components": [
                            {
                                "component_type": "text",
                                "title": "结论",
                                "content": "SP 地区销售额最高。",
                            },
                            {
                                "component_type": "table",
                                "title": "地区明细",
                                "source_task_id": "single_query",
                                "requested_columns": ["seller_state", "gmv"],
                            },
                            {
                                "component_type": "bar_chart",
                                "title": "地区销售额",
                                "source_task_id": "single_query",
                                "dimension": "seller_state",
                                "metrics": ["gmv"],
                            },
                        ],
                    }
                ],
                "limitations": [],
            }
        )
        events: list[dict] = []
        rows = [
            {"seller_state": f"state_{index}", "gmv": index * 100}
            for index in range(20)
        ]
        state = {
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

        result = await generate_final_report(state, _runtime(llm, events))

        report = result["final_report"]
        components = report["sections"][0]["components"]
        table = components[1]
        chart = components[2]
        self.assertEqual(report["status"], "success")
        self.assertEqual(table["data"][0]["seller_state"], "地区 0")
        self.assertEqual(table["data"][0]["gmv"], 0)
        self.assertEqual(chart["columns"][0]["display_name"], "卖家地区")
        self.assertEqual(table["row_count"], 20)
        self.assertFalse(table["truncated"])
        self.assertEqual(
            [event["type"] for event in events],
            ["progress", "report_text_delta", "report_text_delta", "final_report"],
        )
        # 报告模型只接收有限预览，完整 rows 留在后端用于真实绑定。
        self.assertIn("地区 11", llm.prompts[0])
        self.assertNotIn("state_12", llm.prompts[0])
        self.assertNotIn('"rows":', llm.prompts[0])

    async def test_marks_large_bound_data_as_truncated(self) -> None:
        llm = StreamingReportLlm(
            {
                "title": "明细报告",
                "summary": "展示查询明细。",
                "sections": [
                    {
                        "title": "明细",
                        "components": [
                            {
                                "component_type": "table",
                                "title": "全部结果",
                                "source_task_id": "single_query",
                            }
                        ],
                    }
                ],
                "limitations": [],
            }
        )
        events: list[dict] = []
        state = {
            "input_text": "查询全部商品",
            "execution_mode": "single_query",
            "sql_result": [{"product": f"p{index}"} for index in range(201)],
            "result_columns": [
                {"result_name": "product", "field_role": "dimension", "display_name": "商品"}
            ],
        }

        result = await generate_final_report(state, _runtime(llm, events))

        component = result["final_report"]["sections"][0]["components"][0]
        self.assertEqual(component["row_count"], 201)
        self.assertEqual(len(component["data"]), 200)
        self.assertTrue(component["truncated"])
        self.assertTrue(result["final_report"]["limitations"])

    async def test_invalid_model_json_returns_failed_final_report(self) -> None:
        class InvalidLlm:
            async def astream(self, prompt: str):
                yield "这不是 JSON"

        events: list[dict] = []
        state = {
            "input_text": "查询销售额",
            "execution_mode": "single_query",
            "sql_result": [{"gmv": 100}],
        }

        result = await generate_final_report(
            state,
            _runtime(InvalidLlm(), events),
        )

        self.assertEqual(result["final_report"]["status"], "failed")
        self.assertEqual(events[-1]["type"], "final_report")
        self.assertEqual(events[-1]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
