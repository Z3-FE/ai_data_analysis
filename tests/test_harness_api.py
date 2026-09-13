"""主 Harness API 的切片 C HTTP 验收，隔离模型、存储和查询图外部边界。"""

import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.api.routers import harness
from app.main import app
from tests.test_context_engine import FakeMemoryReader, _engine


class ScriptedLLMClient:
    """只替换 API 边界上的模型响应，不替换 PlanningAgent。"""

    def __init__(self, *responses: str) -> None:
        self.responses = iter(responses)
        self.prompts: list[str] = []

    async def ainvoke_auto(self, prompt: str) -> SimpleNamespace:
        self.prompts.append(prompt)
        return SimpleNamespace(content=next(self.responses))


class HarnessApiTest(unittest.TestCase):
    """验证 C 的两轮规划、真实 Runtime 和工具适配器；不启动外部服务。"""

    def request(self, *planner_responses: str, graph_error: Exception | None = None):
        context_engine, _, _ = _engine(FakeMemoryReader())
        self.llm = ScriptedLLMClient(*planner_responses)
        self.events: list[tuple[str, int]] = []
        original_commit = harness._DebugActionCommitter.commit

        async def commit(committer, request):
            result = await original_commit(committer, request)
            self.events.append(("committed", result.action_seq))
            return result

        async def query(state, *, context):
            self.events.append(("query", 1))
            if graph_error is not None:
                raise graph_error
            return {
                "sql": "SELECT amount FROM sales",
                "sql_result": [{"amount": 120}],
                "display_sql_result": [{"amount": 120}],
                "result_columns": [{"result_name": "amount"}],
                "mapping_limitations": [],
            }

        self.query = AsyncMock(side_effect=query)
        with (
            patch(
                "app.api.routers.harness._require_runtime",
                return_value=(
                    FakeMemoryReader(),
                    object(),
                    self.llm,
                ),
            ),
            patch(
                "app.api.routers.harness._ensure_conversation",
                new=AsyncMock(return_value={}),
            ),
            patch(
                "app.api.routers.harness.build_context_engine",
                return_value=context_engine,
            ),
            patch.object(harness.meta_mysql_client_manager, "session_factory", return_value=nullcontext(object())),
            patch.object(harness.dw_mysql_client_manager, "session_factory", return_value=nullcontext(object())),
            patch.object(harness.qdrant_client_manager, "client", object()),
            patch.object(harness.elasticsearch_client_manager, "client", object()),
            patch.object(harness._DebugActionCommitter, "commit", commit),
            patch("app.agent.business_tools.query_data.tool.query_graph.ainvoke", self.query),
        ):
            # 不进入 lifespan，避免 HTTP 回归连接真实数据库和模型服务。
            client = TestClient(app, raise_server_exceptions=False)
            try:
                return client.post(
                    "/api/harness/run",
                    json={"input_text": "分析销售数据"},
                )
            finally:
                client.close()

    def test_final_answer_action_is_committed_and_finalized(self) -> None:
        response = self.request(
            '{"action_type":"final_answer","final_answer":"直接完成"}'
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["phase"], "finalization")
        self.assertEqual(body["final_answer"], "直接完成")
        self.assertEqual(body["planner_action"]["action_type"], "final_answer")
        self.assertEqual(body["planner_action"]["action_seq"], 1)
        self.assertEqual(
            body["action_commit_events"],
            [
                {"stage": "prepared", "action_seq": 1},
                {"stage": "checkpoint", "action_seq": 1},
                {"stage": "committed", "action_seq": 1},
            ],
        )
        self.assertIsNone(body["tool_execution_request"])
        self.assertEqual(body["confirmation_dispatch_count"], 0)

    def test_tool_call_rebuilds_context_before_second_plan(self) -> None:
        response = self.request(
            '{"action_type":"tool_call",'
            '"tool_call":{"tool_name":"query_data","arguments":{"query":"查询销售额"}}}',
            '{"action_type":"final_answer","final_answer":"销售额查询完成"}',
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["planner_action"]["action_type"], "final_answer")
        self.assertEqual(body["planner_action"]["action_seq"], 2)
        self.assertEqual(body["planner_call_count"], 2)
        self.assertEqual(body["iteration"], 1)
        self.assertEqual(body["tool_call_count"], 1)
        self.assertEqual(body["action_commit_result"]["status"], "committed")
        self.assertEqual(body["tool_execution_request"]["action_seq"], 1)
        self.assertEqual(
            body["tool_execution_request"]["tool_call"]["tool_name"],
            "query_data",
        )
        self.assertEqual(body["confirmation_dispatch_count"], 0)
        self.assertEqual(body["final_answer"], "销售额查询完成")
        self.assertEqual(body["action_commit_events"], [
            {"stage": stage, "action_seq": seq}
            for seq in (1, 2)
            for stage in ("prepared", "checkpoint", "committed")
        ])
        self.assertEqual(self.events, [("committed", 1), ("query", 1), ("committed", 2)])
        self.query.assert_awaited_once()
        query_state = self.query.call_args.args[0]
        self.assertEqual(query_state["input_text"], "查询销售额")
        for key, value in body["run_ref"].items():
            self.assertEqual(query_state[key], value)
        observation = body["planner_input"]["state_view"]["observations"][0]
        self.assertEqual(observation["status"], "success")
        self.assertEqual(observation["action_id"], body["tool_execution_request"]["tool_call"]["action_id"])
        builds = [s["harness"]["last_context_build_id"] for s in body["snapshots"] if s["harness"]["phase"] == "plan"]
        self.assertEqual(len(builds), 2)
        self.assertNotEqual(builds[0], builds[1])
        self.assertIn(observation["summary"], self.llm.prompts[1])
        self.assertNotIn("sql_result", self.llm.prompts[1])

    def test_ask_user_is_rejected_without_commit_until_slice_d(self) -> None:
        response = self.request(
            '{"action_type":"ask_user",'
            '"ask_user":{"question":"哪个月份？",'
            '"reason_code":"missing_condition"}}'
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.events, [])
        self.query.assert_not_awaited()

    def test_invalid_planner_output_does_not_return_success(self) -> None:
        response = self.request(
            '{"action_type":"tool_call",'
            '"tool_call":{"tool_name":"unknown_tool","arguments":{}}}'
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(self.events, [])
        self.query.assert_not_awaited()

    def test_query_failure_does_not_report_completion_or_replan(self) -> None:
        response = self.request(
            '{"action_type":"tool_call","tool_call":{"tool_name":"query_data",' 
            '"arguments":{"query":"查询销售额"}}}',
            graph_error=RuntimeError("query failed"),
        )
        self.assertEqual(response.status_code, 500)
        self.assertEqual(len(self.llm.prompts), 1)
        self.assertEqual(self.events, [("committed", 1), ("query", 1)])
        self.query.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
