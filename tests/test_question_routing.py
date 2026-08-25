"""问题路由节点的纯单元测试。"""

import unittest
from types import SimpleNamespace

from langchain_core.runnables import RunnableLambda

from app.agent.nodes.plan_analysis import plan_analysis
from app.agent.nodes.route_question import route_question


def _llm_response(payload: str) -> RunnableLambda:
    """构造返回固定 JSON 的测试模型。"""
    return RunnableLambda(lambda _: payload)


class QuestionRoutingTest(unittest.IsolatedAsyncioTestCase):
    """验证三种执行模式及其图分支。"""

    async def test_analysis_question_generates_analysis_plan(self) -> None:
        responses = iter(
            [
                '{"execution_mode":"analysis",'
                '"reason":"需要多个证据并进行解释",'
                '"analysis_goals":["比较变化","识别贡献因素"],'
                '"confidence":0.92}',
                '{"analysis_summary":"识别销售额下降并分析贡献因素",'
                '"tasks":[{"task_id":"monthly_sales",'
                '"question":"查询各月销售额",'
                '"purpose":"计算月度变化","depends_on":[]}]}',
            ]
        )
        runtime = SimpleNamespace(
            context={"llm_client": RunnableLambda(lambda _: next(responses))},
            stream_writer=lambda _: None,
        )
        state = {"input_text": "为什么今年华东销售额下降"}
        state.update(await route_question(state, runtime))
        result = await plan_analysis(state, runtime)

        self.assertEqual(state["execution_mode"], "analysis")
        self.assertEqual(state["analysis_goals"], ["比较变化", "识别贡献因素"])
        self.assertEqual(
            result["analysis_plan"]["analysis_summary"],
            "识别销售额下降并分析贡献因素",
        )
        self.assertEqual(
            result["analysis_plan"]["tasks"][0]["task_id"], "monthly_sales"
        )

    async def test_clarification_mode_returns_clarification_question(self) -> None:
        result = await route_question(
            {"input_text": "帮我看看销售额"},
            SimpleNamespace(
                context={
                    "llm_client": _llm_response(
                        '{"execution_mode":"clarification",'
                        '"reason":"缺少时间范围",'
                        '"analysis_goals":[],'
                        '"confidence":0.85,'
                        '"clarification_question":"请补充时间范围"}'
                    )
                },
                stream_writer=lambda _: None,
            ),
        )

        self.assertEqual(result["execution_mode"], "clarification")
        self.assertEqual(result["clarification_question"], "请补充时间范围")

    async def test_invalid_model_output_falls_back_to_single_query(self) -> None:
        result = await route_question(
            {"input_text": "查询销售额"},
            SimpleNamespace(
                context={"llm_client": _llm_response("[]")},
                stream_writer=lambda _: None,
            ),
        )

        self.assertEqual(result["execution_mode"], "single_query")
        self.assertEqual(result["route_confidence"], 0.0)


if __name__ == "__main__":
    unittest.main()
