"""切片 B PlanningAgent 的解析、校验和服务端发行测试。"""

import unittest
from types import SimpleNamespace

from app.agent.daily_chat_agent import DailyChatAgent, is_greeting
from app.agent.context_engine.contracts import ContextRequest
from app.agent.planning_agent.agent import PlanningAgent
from app.agent.planning_agent.contracts import ActionIssuanceContext
from app.agent.planning_agent.errors import PlannerFailure
from app.agent.state_result_store.contracts import (
    ActionType,
    PlannerCapabilities,
    PlannerInput,
    PlannerStateView,
    ToolSpec,
)
from tests.test_context_engine import FakeMemoryReader, _engine


class ScriptedPlannerClient:
    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    async def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


class PlanningAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_greeting_uses_daily_chat_without_planner(self) -> None:
        class ChatClient:
            async def ainvoke_auto(inner_self, messages):
                inner_self.messages = messages
                return SimpleNamespace(content="你好！有什么可以帮你？")

        client = ChatClient()
        planner = ScriptedPlannerClient("不应调用 Planner")
        agent = PlanningAgent(llm_client=planner, daily_chat_agent=DailyChatAgent(client))
        value = self.planner_input().model_copy(
            update={"state_view": PlannerStateView(original_goal="你好", iteration=0)}
        )
        action = await agent.plan(value, issuance=self.issuance())
        self.assertEqual(action.action_type, ActionType.FINAL_ANSWER)
        self.assertEqual(action.final_answer, "你好！有什么可以帮你？")
        self.assertEqual(planner.prompts, [])
        self.assertEqual(client.messages, list(self.context.messages))

    async def test_planner_can_select_daily_chat_for_general_questions(self) -> None:
        class ChatClient:
            async def ainvoke_auto(self, messages):
                return SimpleNamespace(content="我可以帮你查询和分析数据。")

        agent = PlanningAgent(
            llm_client=ScriptedPlannerClient('{"action_type":"daily_chat"}'),
            daily_chat_agent=DailyChatAgent(ChatClient()),
        )
        action = await agent.plan(self.planner_input(), issuance=self.issuance())
        self.assertEqual(action.action_type, ActionType.FINAL_ANSWER)
        self.assertEqual(action.action_seq, 1)

    async def test_daily_chat_requires_enabled_answer_capability(self) -> None:
        agent = PlanningAgent(
            llm_client=ScriptedPlannerClient('{"action_type":"daily_chat"}'),
            capabilities=PlannerCapabilities(allow_final_answer=False),
        )
        with self.assertRaises(PlannerFailure):
            await agent.plan(self.planner_input(), issuance=self.issuance())

    def test_greeting_shortcut_does_not_match_data_requests(self) -> None:
        self.assertTrue(is_greeting("你好！"))
        self.assertTrue(is_greeting(" Hi! "))
        self.assertFalse(is_greeting("你好，帮我查询销售额"))
        self.assertFalse(is_greeting("继续"))

    async def asyncSetUp(self) -> None:
        engine, _, _ = _engine(FakeMemoryReader())
        self.context = await engine.build(
            ContextRequest(
                user_id="user-1",
                conversation_id="conversation-1",
                query="分析销售数据",
                system_instructions="测试",
            )
        )

    def planner_input(self, *, tool_specs: tuple[ToolSpec, ...] = ()) -> PlannerInput:
        return PlannerInput(
            compiled_context=self.context,
            state_view=PlannerStateView(original_goal="分析销售数据", iteration=0),
            tool_specs=tool_specs,
        )

    def issuance(self, action_seq: int = 1) -> ActionIssuanceContext:
        return ActionIssuanceContext(run_id="run-1", iteration=0, action_seq=action_seq)

    async def test_three_action_json_payloads_are_parsed(self) -> None:
        cases = (
            (
                '{"action_type":"final_answer","final_answer":"完成"}',
                ActionType.FINAL_ANSWER,
            ),
            (
                '{"action_type":"ask_user","ask_user":{"question":"哪个月份？","reason_code":"missing_condition"}}',
                ActionType.ASK_USER,
            ),
            (
                '{"action_type":"tool_call","tool_call":{"tool_name":"query_data","arguments":{"query":"销售额"}}}',
                ActionType.TOOL_CALL,
            ),
        )
        tool = ToolSpec(
            name="query_data",
            description="查询数据",
            permission="data.read",
            input_schema={
                "type": "object",
                "required": ["query"],
                "properties": {"query": {"type": "string"}},
            },
        )
        for raw, expected_type in cases:
            with self.subTest(expected_type=expected_type):
                agent = PlanningAgent(
                    llm_client=ScriptedPlannerClient(raw),
                    capabilities=PlannerCapabilities(allow_ask_user=True),
                )
                action = await agent.plan(
                    self.planner_input(tool_specs=(tool,)),
                    issuance=self.issuance(),
                )
                self.assertEqual(action.action_type, expected_type)
                self.assertEqual(action.action_seq, 1)

    async def test_server_owns_action_sequence_and_tool_id(self) -> None:
        tool = ToolSpec(name="query_data", description="查询数据", permission="data.read")
        agent = PlanningAgent(
            llm_client=ScriptedPlannerClient(
                '{"action_type":"tool_call","tool_call":{"tool_name":"query_data","arguments":{}}}'
            )
        )
        action = await agent.plan(
            self.planner_input(tool_specs=(tool,)),
            issuance=self.issuance(action_seq=3),
        )
        self.assertEqual(action.action_seq, 3)
        self.assertEqual(action.tool_call.action_id, "run-1:i0:a3")

        forged = PlanningAgent(
            llm_client=ScriptedPlannerClient(
                '{"action_type":"tool_call","action_seq":999,"tool_call":{"action_id":"model-id","tool_name":"query_data","arguments":{}}}'
            )
        )
        with self.assertRaises(PlannerFailure):
            await forged.plan(
                self.planner_input(tool_specs=(tool,)), issuance=self.issuance()
            )

    async def test_unknown_tool_and_invalid_arguments_fail(self) -> None:
        unknown = PlanningAgent(
            llm_client=ScriptedPlannerClient(
                '{"action_type":"tool_call","tool_call":{"tool_name":"other","arguments":{}}}'
            )
        )
        with self.assertRaises(PlannerFailure):
            await unknown.plan(self.planner_input(), issuance=self.issuance())

        invalid = PlanningAgent(
            llm_client=ScriptedPlannerClient(
                '{"action_type":"tool_call","tool_call":{"tool_name":"query_data","arguments":{}}}'
            )
        )
        tool = ToolSpec(
            name="query_data",
            description="查询数据",
            permission="data.read",
            input_schema={"type": "object", "required": ["query"]},
        )
        with self.assertRaises(PlannerFailure):
            await invalid.plan(
                self.planner_input(tool_specs=(tool,)), issuance=self.issuance()
            )

    async def test_ask_user_capability_is_enforced(self) -> None:
        agent = PlanningAgent(
            llm_client=ScriptedPlannerClient(
                '{"action_type":"ask_user","ask_user":{"question":"请确认","reason_code":"missing_condition"}}'
            )
        )
        with self.assertRaises(PlannerFailure):
            await agent.plan(self.planner_input(), issuance=self.issuance())


if __name__ == "__main__":
    unittest.main()
