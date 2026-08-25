"""分析任务执行节点测试。"""

import unittest
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langchain_core.runnables import RunnableLambda

from app.agent.nodes.execute_analysis import (
    _build_data_profile,
    _normalize_calculation_result,
    _validate_decrease_result,
    execute_analysis,
)


class FakeQueryGraph:
    """返回固定 SQL 查询结果的问数图。"""

    async def astream(self, state, context, stream_mode):
        yield (
            "custom",
            {
                "type": "progress",
                "step": "执行 SQL",
                "status": "running",
            },
        )
        yield (
            "values",
            {
                "sql": "SELECT year_month_value, sales_total FROM fact",
                "sql_result": [
                    {"year_month_value": "2017-01", "sales_total": 100},
                    {"year_month_value": "2017-02", "sales_total": 80},
                ],
            },
        )

    async def ainvoke(self, state, context):
        return {
            "sql": "SELECT year_month_value, sales_total FROM fact",
            "sql_result": [
                {"year_month_value": "2017-01", "sales_total": 100},
                {"year_month_value": "2017-02", "sales_total": 80},
            ],
        }


class FakeDependencyQueryGraph:
    """根据具体化后的问题返回不同任务的查询结果。"""

    def __init__(self) -> None:
        self.questions = []

    async def astream(self, state, context, stream_mode):
        question = state["input_text"]
        self.questions.append(question)
        if "商品类别" in question:
            rows = [
                {"month": "2017-11", "category": "A", "sales": 100},
                {"month": "2017-12", "category": "A", "sales": 60},
            ]
        elif "卖家地区" in question:
            rows = [
                {"month": "2017-11", "region": "SP", "sales": 90},
                {"month": "2017-12", "region": "SP", "sales": 70},
            ]
        else:
            rows = [
                {"month": "2017-11", "sales": 200},
                {"month": "2017-12", "sales": 120},
            ]
        yield "values", {"sql": f"SQL: {question}", "sql_result": rows}


class ExecuteAnalysisTest(unittest.IsolatedAsyncioTestCase):
    """验证查询结果会被交给 LLM，再执行生成的 Python。"""

    async def test_generates_python_from_actual_query_columns(self) -> None:
        llm_payload = json.dumps(
            {
                "code": (
                    "def calculate(rows):\n"
                    '    return {"decrease_amount": rows[0]["sales_total"] - rows[1]["sales_total"]}'
                ),
                "result_description": "返回下降金额",
            },
            ensure_ascii=False,
        )
        runtime = SimpleNamespace(
            context={"llm_client": RunnableLambda(lambda _: llm_payload)},
            stream_writer=lambda _: None,
        )
        state = {
            "input_text": "找出销售额下降最明显的月份",
            "analysis_plan": {
                "tasks": [
                    {
                        "task_id": "monthly_sales",
                        "question": "查询每月销售额",
                        "purpose": "计算月份下降金额",
                        "depends_on": [],
                    }
                ]
            },
        }

        with (
            patch(
                "app.agent.nodes.execute_analysis.query_graph",
                FakeQueryGraph(),
            ),
            patch(
                "app.agent.nodes.execute_analysis.execute_python_calculation",
                new=AsyncMock(return_value={"decrease_amount": 20}),
            ),
        ):
            result = await execute_analysis(state, runtime)

        task_result = result["analysis_task_results"][0]
        self.assertEqual(task_result["status"], "success")
        self.assertEqual(task_result["columns"][1]["name"], "sales_total")
        self.assertIn("sales_total", task_result["python_code"])
        self.assertEqual(
            task_result["calculation_result"], {"decrease_amount": 20}
        )
        self.assertEqual(task_result["data_profile"]["row_mode"], "full")
        self.assertEqual(result["analysis_evidence"]["status"], "success")
        self.assertEqual(
            result["analysis_evidence"]["successful_task_ids"],
            ["monthly_sales"],
        )
        evidence_task = result["analysis_evidence"]["task_results"][0]
        self.assertEqual(evidence_task["row_count"], 2)
        self.assertNotIn("rows", evidence_task)
        self.assertNotIn("data_profile", evidence_task)
        self.assertNotIn("python_code", evidence_task)

    async def test_executes_dependent_tasks_after_resolving_conditions(self) -> None:
        def llm_response(prompt):
            text = prompt.to_string()
            if "数据分析任务解析器" in text:
                if "商品类别" in text:
                    question = "查询2017-11和2017-12各商品类别的销售额"
                else:
                    question = "查询2017-11和2017-12各卖家地区的销售额"
                return json.dumps({"question": question}, ensure_ascii=False)
            return json.dumps(
                {
                    "code": "def calculate(rows):\n    return {}",
                    "result_description": "返回计算结果",
                },
                ensure_ascii=False,
            )

        async def calculation_result(code, rows):
            if "category" in rows[0]:
                return {"category": "A", "decrease_amount": 40}
            if "region" in rows[0]:
                return {"region": "SP", "decrease_amount": 20}
            return {
                "target_month": "2017-12",
                "previous_month": "2017-11",
                "decrease_amount": 80,
            }

        runtime = SimpleNamespace(
            context={"llm_client": RunnableLambda(llm_response)},
            stream_writer=lambda _: None,
        )
        state = {
            "input_text": "找出2017年销售额下降最明显的月份并分析原因",
            "analysis_plan": {
                "tasks": [
                    {
                        "task_id": "monthly_sales",
                        "question": "查询2016-12至2017-12每月销售额",
                        "purpose": "找出下降最多的月份",
                        "depends_on": [],
                    },
                    {
                        "task_id": "category_sales",
                        "question": "查询目标月份及上月各商品类别销售额",
                        "purpose": "找出下降最多的商品类别",
                        "depends_on": ["monthly_sales"],
                    },
                    {
                        "task_id": "region_sales",
                        "question": "查询目标月份及上月各卖家地区销售额",
                        "purpose": "找出下降最多的卖家地区",
                        "depends_on": ["monthly_sales"],
                    },
                ]
            },
        }
        query_graph = FakeDependencyQueryGraph()

        with (
            patch("app.agent.nodes.execute_analysis.query_graph", query_graph),
            patch(
                "app.agent.nodes.execute_analysis.execute_python_calculation",
                new=AsyncMock(side_effect=calculation_result),
            ),
        ):
            result = await execute_analysis(state, runtime)

        task_results = result["analysis_task_results"]
        self.assertEqual([item["status"] for item in task_results], ["success"] * 3)
        self.assertEqual(len(query_graph.questions), 3)
        self.assertIn("2017-11和2017-12", task_results[1]["resolved_question"])
        self.assertIn("2017-11和2017-12", task_results[2]["resolved_question"])
        evidence = result["analysis_evidence"]
        self.assertEqual(evidence["status"], "success")
        self.assertEqual(
            evidence["successful_task_ids"],
            ["monthly_sales", "category_sales", "region_sales"],
        )
        self.assertEqual(evidence["task_results"][1]["depends_on"], ["monthly_sales"])
        self.assertNotIn("rows", evidence["task_results"][1])

    async def test_marks_calculation_error_as_failed(self) -> None:
        runtime = SimpleNamespace(
            context={
                "llm_client": RunnableLambda(
                    lambda _: json.dumps(
                        {
                            "code": "def calculate(rows):\n    return {}",
                            "result_description": "返回计算结果",
                        }
                    )
                )
            },
            stream_writer=lambda _: None,
        )
        state = {
            "input_text": "分析销售额变化",
            "analysis_plan": {
                "tasks": [
                    {
                        "task_id": "monthly_sales",
                        "question": "查询每月销售额",
                        "purpose": "计算销售额变化",
                        "depends_on": [],
                    }
                ]
            },
        }

        with (
            patch("app.agent.nodes.execute_analysis.query_graph", FakeQueryGraph()),
            patch(
                "app.agent.nodes.execute_analysis.execute_python_calculation",
                new=AsyncMock(return_value={"error": "数据不足"}),
            ),
        ):
            result = await execute_analysis(state, runtime)

        task_result = result["analysis_task_results"][0]
        self.assertEqual(task_result["status"], "failed")
        self.assertIn("数据不足", task_result["error"])
        self.assertEqual(result["analysis_evidence"]["status"], "failed")
        self.assertEqual(
            result["analysis_evidence"]["failed_task_ids"],
            ["monthly_sales"],
        )


class DataProfileTest(unittest.TestCase):
    """验证画像来自完整数据，同时限制传给 LLM 的明细行数。"""

    def test_profiles_small_dataset_with_decimal_and_null(self) -> None:
        rows = [
            {"month": "2017-11", "sales": Decimal("10.90")},
            {"month": "2017-12", "sales": None},
        ]

        profile = _build_data_profile(rows)

        self.assertEqual(profile["row_count"], 2)
        self.assertEqual(profile["row_mode"], "full")
        self.assertEqual(profile["profile_rows"], rows)
        sales = profile["columns"][1]
        self.assertEqual(sales["types"], ["Decimal"])
        self.assertTrue(sales["nullable"])
        self.assertEqual(sales["null_count"], 1)
        self.assertEqual(sales["min"], 10.9)
        self.assertEqual(sales["max"], 10.9)

    def test_profiles_all_rows_but_limits_large_dataset_samples(self) -> None:
        rows = [
            {"category": f"category_{index}", "sales": index}
            for index in range(100)
        ]

        profile = _build_data_profile(rows)

        self.assertEqual(profile["row_count"], 100)
        self.assertEqual(profile["row_mode"], "representative_sample")
        self.assertEqual(len(profile["profile_rows"]), 12)
        self.assertEqual(profile["columns"][0]["distinct_count"], 100)
        self.assertEqual(profile["columns"][1]["max"], 99.0)


class CalculationResultTest(unittest.TestCase):
    """验证动态计算结果的数值归一化和下降结果边界。"""

    def test_normalizes_business_amounts_and_rates(self) -> None:
        result = _normalize_calculation_result(
            {
                "difference": -38906.689999999995,
                "decrease_amount": 38906.689999999995,
                "change_rate": -0.23514232448455247,
                "details": [{"metric_value": 10.123456789}],
            }
        )

        self.assertEqual(result["difference"], -38906.69)
        self.assertEqual(result["decrease_amount"], 38906.69)
        self.assertEqual(result["change_rate"], -0.235142)
        self.assertEqual(result["details"][0]["metric_value"], 10.123457)

    def test_rejects_decrease_result_without_real_decrease(self) -> None:
        task = {
            "question": "找出下降最多的商品类别",
            "purpose": "计算商品类别下降金额",
        }

        with self.assertRaisesRegex(ValueError, "没有发现真实下降"):
            _validate_decrease_result(
                {"category": "A", "decrease_amount": 0},
                task,
            )

    def test_rejects_decrease_result_without_decrease_amount(self) -> None:
        task = {
            "question": "找出下降最多的商品类别",
            "purpose": "计算商品类别下降金额",
        }

        with self.assertRaisesRegex(ValueError, "缺少 decrease_amount"):
            _validate_decrease_result({"category": "A"}, task)


if __name__ == "__main__":
    unittest.main()
