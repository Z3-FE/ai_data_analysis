"""分析计划结构的校验测试。"""

import unittest

from pydantic import ValidationError

from app.agent.nodes.plan_analysis import AnalysisPlan


class AnalysisPlanTest(unittest.TestCase):
    """验证分析任务的标识和依赖关系。"""

    def test_accepts_dependency_on_previous_task(self) -> None:
        plan = AnalysisPlan.model_validate(
            {
                "analysis_summary": "识别下降月份并下钻品类",
                "tasks": [
                    {
                        "task_id": "monthly_sales",
                        "question": "查询各月销售额",
                        "purpose": "计算环比变化",
                        "depends_on": [],
                    },
                    {
                        "task_id": "category_contribution",
                        "question": "查询目标月份及上月各品类销售额",
                        "purpose": "计算品类下降贡献",
                        "depends_on": ["monthly_sales"],
                    },
                ],
            }
        )

        self.assertEqual(plan.tasks[1].depends_on, ["monthly_sales"])

    def test_rejects_dependency_on_later_task(self) -> None:
        with self.assertRaises(ValidationError):
            AnalysisPlan.model_validate(
                {
                    "analysis_summary": "无效计划",
                    "tasks": [
                        {
                            "task_id": "category_contribution",
                            "question": "查询品类销售额",
                            "purpose": "计算品类下降贡献",
                            "depends_on": ["monthly_sales"],
                        },
                        {
                            "task_id": "monthly_sales",
                            "question": "查询各月销售额",
                            "purpose": "计算环比变化",
                            "depends_on": [],
                        },
                    ],
                }
            )

    def test_rejects_blank_task_text(self) -> None:
        with self.assertRaises(ValidationError):
            AnalysisPlan.model_validate(
                {
                    "analysis_summary": " ",
                    "tasks": [
                        {
                            "task_id": "monthly_sales",
                            "question": "",
                            "purpose": "计算环比",
                            "depends_on": [],
                        }
                    ],
                }
            )

    def test_rejects_duplicate_dependencies(self) -> None:
        with self.assertRaises(ValidationError):
            AnalysisPlan.model_validate(
                {
                    "analysis_summary": "测试重复依赖",
                    "tasks": [
                        {
                            "task_id": "monthly_sales",
                            "question": "查询销售额",
                            "purpose": "提供月份数据",
                            "depends_on": [],
                        },
                        {
                            "task_id": "category_sales",
                            "question": "查询品类销售额",
                            "purpose": "分析品类变化",
                            "depends_on": ["monthly_sales", "monthly_sales"],
                        },
                    ],
                }
            )


if __name__ == "__main__":
    unittest.main()
