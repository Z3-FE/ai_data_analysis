"""Harness API 的真实 PostgreSQL 组件边界测试。"""

import copy
import hashlib
import json
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.agent.loop_controller.contracts import (
    ConfirmationResolution,
    FinalizationResult,
)
from app.agent.state_result_store.contracts import (
    ConfirmationStatus,
    HarnessRunRef,
    HarnessStateSnapshot,
    HarnessStatus,
    LoopPhase,
)
from app.agent.state_result_store.state import transition_harness_state
from app.agent.tool_runtime.artifacts import (
    ArtifactReadRequest,
    ArtifactRecord,
    ArtifactWriteRequest,
)
from app.api.routers import harness
from app.main import app
from app.repositories.harness_run_repository import HarnessPersistenceError
from tests.test_context_engine import FakeMemoryReader, _engine


class AsyncContext:
    """为 API 测试提供异步数据库会话工厂替身。"""

    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False


class ScriptedLLMClient:
    """只替换模型响应，不替换生产 PlanningAgent。"""

    def __init__(self) -> None:
        self.responses = iter(())
        self.prompts: list[str] = []

    def set_responses(self, *responses: str) -> None:
        self.responses = iter(responses)

    async def ainvoke_auto(self, prompt: str) -> SimpleNamespace:
        self.prompts.append(prompt)
        return SimpleNamespace(content=next(self.responses))


class FakeConversationRepository:
    """隔离会话历史写入边界；不替换 Harness 的 PostgreSQL 运行存储。"""

    def __init__(self, session_factory) -> None:
        self.turns: list[dict] = []
        self.finished: list[dict] = []

    async def start_turn(self, **values) -> bool:
        self.turns.append(values)
        return True

    async def finish_turn(self, **values) -> bool:
        self.finished.append(values)
        return True


class FakeFinalizationService:
    """记录收口调用并返回正式 FinalizationResult。"""

    def __init__(self, conversation_repository, run_store) -> None:
        self.conversation_repository = conversation_repository
        self.run_store = run_store
        self.calls: list = []

    async def finalize(self, value) -> FinalizationResult:
        self.calls.append(value)
        await self.conversation_repository.finish_turn(
            conversation_id=value.run_ref.conversation_id,
            user_id=value.run_ref.user_id,
            turn_id=value.run_ref.turn_id,
            execution_mode="harness",
            status=value.terminal_status.value,
            assistant_content=value.final_answer,
            output_type="text",
            output_payload={"message": value.final_answer},
        )
        state = await self.run_store.load(value.run_ref)
        state["harness"] = transition_harness_state(
            state["harness"],
            status=value.terminal_status,
            phase=LoopPhase.FINALIZATION,
            terminal_intent=value.terminal_status.value,
        )
        await self.run_store.save(value.run_ref, state)
        snapshot = HarnessStateSnapshot.model_validate(state["harness"])
        return FinalizationResult(
            run_ref=value.run_ref,
            status=value.terminal_status,
            final_answer=value.final_answer,
            iteration=snapshot.iteration,
            last_error=snapshot.last_error,
        )


class FakeActionCommitter:
    """记录动作提交，不把旧的 DebugActionCommitter 带回生产代码。"""

    def __init__(self, session_factory) -> None:
        self.calls = []

    async def commit(self, request):
        self.calls.append(request)
        action_id = request.action.tool_call.action_id if request.action.tool_call else None
        return SimpleNamespace(
            status="committed",
            action_seq=request.action.action_seq,
            action_type=request.action.action_type,
            action_id=action_id,
        )


class FakeArtifactStore:
    """只保存测试中的完整 Artifact，验证 Planner 不会直接拿到 rows。"""

    def __init__(self, session_factory) -> None:
        self.records: dict[str, ArtifactRecord] = {}

    async def save(self, request: ArtifactWriteRequest) -> ArtifactRecord:
        payload_bytes = json.dumps(
            request.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        payload_hash = hashlib.sha256(payload_bytes).hexdigest()
        now = datetime.now(UTC)
        record = ArtifactRecord(
            artifact_id=payload_hash,
            result_ref=f"artifact:{payload_hash}",
            run_ref=request.run_ref,
            action_id=request.action_id,
            tool_name=request.tool_name,
            artifact_kind=request.artifact_kind,
            payload=request.payload,
            payload_hash=payload_hash,
            size_bytes=len(payload_bytes),
            created_at=now,
            expires_at=now + timedelta(days=1),
        )
        self.records[record.result_ref] = record
        return record

    async def read(self, request: ArtifactReadRequest) -> ArtifactRecord:
        return self.records[request.result_ref]


class FakePersistentRunStore:
    """提供可跨 HTTP 请求复用的 PostgreSQL 运行状态语义。"""

    def __init__(self, session_factory) -> None:
        self.states: dict[str, dict] = {}
        self.confirmations: dict[str, dict] = {}
        self.snapshots: list[dict] = []

    async def create(self, run_ref: HarnessRunRef, state: dict) -> None:
        if run_ref.run_id in self.states:
            raise HarnessPersistenceError("Harness run 已存在")
        self.states[run_ref.run_id] = copy.deepcopy(state)
        self.snapshots.append(copy.deepcopy(state))

    async def save(self, run_ref: HarnessRunRef, state: dict) -> None:
        self._assert_identity(run_ref)
        self.states[run_ref.run_id] = copy.deepcopy(state)
        self.snapshots.append(copy.deepcopy(state))

    async def load(self, run_ref: HarnessRunRef) -> dict:
        self._assert_identity(run_ref)
        return copy.deepcopy(self.states[run_ref.run_id])

    async def load_by_id(self, *, run_id: str, user_id: str) -> dict:
        state = self.states.get(run_id)
        if state is None or state["user_id"] != user_id:
            raise HarnessPersistenceError("Harness run 不存在")
        return copy.deepcopy(state)

    async def pause_for_confirmation(self, run_ref, state, record) -> None:
        self._assert_identity(run_ref)
        self.states[run_ref.run_id] = copy.deepcopy(state)
        self.confirmations[record.request.confirmation_id] = {
            "request_digest": record.request_digest,
            "status": ConfirmationStatus.PENDING.value,
            "reply_digest": None,
        }
        self.snapshots.append(copy.deepcopy(state))

    async def resolve_confirmation(self, run_ref, reply) -> ConfirmationResolution:
        self._assert_identity(run_ref)
        record = self.confirmations.get(reply.confirmation_id)
        if record is None:
            raise HarnessPersistenceError("确认记录不存在")
        reply_digest = hashlib.sha256(
            json.dumps(
                reply.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        state = copy.deepcopy(self.states[run_ref.run_id])
        if record["status"] != ConfirmationStatus.PENDING.value:
            if record["reply_digest"] == reply_digest:
                return ConfirmationResolution(status="idempotent", state=state)
            raise HarnessPersistenceError("确认请求已被不同回复处理")

        harness_state = state["harness"]
        if harness_state["status"] != "waiting_confirmation":
            raise HarnessPersistenceError("当前运行不在 waiting_confirmation 状态")
        pending = harness_state["pending_confirmation"]
        if pending["confirmation_id"] != reply.confirmation_id:
            raise HarnessPersistenceError("confirmation_id 与当前运行不匹配")
        record["status"] = (
            ConfirmationStatus.CONFIRMED.value
            if reply.decision == "confirm"
            else ConfirmationStatus.REJECTED.value
        )
        record["reply_digest"] = reply_digest
        if reply.decision == "confirm":
            harness_state["last_confirmation_answer"] = reply.answer
            harness_state["resolved_conditions"] = {
                **harness_state.get("resolved_conditions", {}),
                **reply.resolved_conditions,
            }
        harness_state["confirmation_attempt_count"] = int(
            harness_state.get("confirmation_attempt_count", 0)
        ) + 1
        harness_state["last_confirmation_reason_code"] = pending["reason_code"]
        harness_state["last_confirmation_question"] = pending["question"]
        state["harness"] = transition_harness_state(
            harness_state,
            status=HarnessStatus.RUNNING,
            phase=LoopPhase.RESTORE_RUN,
        )
        self.states[run_ref.run_id] = copy.deepcopy(state)
        self.snapshots.append(copy.deepcopy(state))
        return ConfirmationResolution(
            status="confirmed" if reply.decision == "confirm" else "rejected",
            state=state,
        )

    def _assert_identity(self, run_ref: HarnessRunRef) -> None:
        state = self.states.get(run_ref.run_id)
        if state is None:
            raise HarnessPersistenceError("Harness run 不存在")
        for field, value in run_ref.model_dump().items():
            if state.get(field) != value:
                raise HarnessPersistenceError(f"Harness 运行身份不匹配: {field}")


class HarnessApiTest(unittest.TestCase):
    """验证生产 API 的运行、暂停、恢复、幂等和 Artifact 边界。"""

    def setUp(self) -> None:
        self.context_engine, _, _ = _engine(FakeMemoryReader())
        self.llm = ScriptedLLMClient()
        self.run_store = FakePersistentRunStore(object())
        self.action_committer = FakeActionCommitter(object())
        self.conversation = FakeConversationRepository(object())
        self.finalization = FakeFinalizationService(self.conversation, self.run_store)
        self.artifacts = FakeArtifactStore(object())
        self.query = AsyncMock(side_effect=self._query)
        self.graph_error: Exception | None = None

        self.patches = [
            patch(
                "app.api.routers.harness._require_runtime",
                return_value=(
                    SimpleNamespace(manager=FakeMemoryReader(), formation_service=None),
                    object(),
                    self.llm,
                ),
            ),
            patch(
                "app.api.routers.harness._ensure_conversation",
                new=AsyncMock(return_value={}),
            ),
            patch("app.api.routers.harness.ConversationRepository", return_value=self.conversation),
            patch("app.api.routers.harness.build_context_engine", return_value=self.context_engine),
            patch("app.api.routers.harness.PostgresHarnessRunStore", return_value=self.run_store),
            patch("app.api.routers.harness.PostgresActionCommitter", return_value=self.action_committer),
            patch("app.api.routers.harness.PostgresResultArtifactStore", return_value=self.artifacts),
            patch("app.api.routers.harness.PostgresFinalizationService", return_value=self.finalization),
            patch.object(harness.meta_mysql_client_manager, "session_factory", lambda: AsyncContext(object())),
            patch.object(harness.dw_mysql_client_manager, "session_factory", lambda: AsyncContext(object())),
            patch.object(harness.qdrant_client_manager, "client", object()),
            patch.object(harness.elasticsearch_client_manager, "client", object()),
            patch("app.agent.business_tools.query_data.tool.query_graph.astream", new=self._query_stream),
        ]
        for item in self.patches:
            item.start()
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self) -> None:
        self.client.close()
        for item in reversed(self.patches):
            item.stop()

    def _query(self, state, *, context):
        if self.graph_error is not None:
            raise self.graph_error
        return {
            "sql": "SELECT amount FROM sales",
            "sql_result": [{"amount": 120}],
            "display_sql_result": [{"amount": 120}],
            "result_columns": [{"result_name": "amount"}],
            "mapping_limitations": [],
        }

    async def _query_stream(self, state, *, context, stream_mode):
        self.assertEqual(stream_mode, ["custom", "values"])
        result = await self.query(state, context=context)
        yield ("custom", {"type": "progress", "node": "execute_sql", "status": "success"})
        yield ("values", result)

    def post_run(self, *planner_responses: str):
        self.llm.set_responses(*planner_responses)
        return self.client.post(
            "/api/harness/run",
            json={"input_text": "分析销售数据", "user_id": "user-1"},
        )

    def post_run_stream(self, *planner_responses: str):
        self.llm.set_responses(*planner_responses)
        return self.client.post(
            "/api/harness/run/stream",
            json={"input_text": "分析销售数据", "user_id": "user-1"},
        )

    def post_resume(self, run_id: str, confirmation_id: str, *, answer="2026 年", decision="confirm", resolved_conditions=None, user_id="user-1"):
        return self.client.post(
            "/api/harness/run/resume",
            json={
                "run_id": run_id,
                "user_id": user_id,
                "confirmation_id": confirmation_id,
                "answer": answer,
                "decision": decision,
                "resolved_conditions": resolved_conditions or {},
            },
        )

    def post_resume_stream(
        self,
        run_id: str,
        confirmation_id: str,
        *,
        answer="2026 年",
        decision="confirm",
        resolved_conditions=None,
        user_id="user-1",
    ):
        return self.client.post(
            "/api/harness/run/resume/stream",
            json={
                "run_id": run_id,
                "user_id": user_id,
                "confirmation_id": confirmation_id,
                "answer": answer,
                "decision": decision,
                "resolved_conditions": resolved_conditions or {},
            },
        )

    @staticmethod
    def parse_sse_events(response) -> list[dict]:
        events = []
        for frame in response.text.split("\n\n"):
            data = "\n".join(
                line.removeprefix("data:").lstrip()
                for line in frame.splitlines()
                if line.startswith("data:")
            )
            if data:
                events.append(json.loads(data))
        return events

    def test_final_answer_uses_production_persistence_boundaries(self) -> None:
        response = self.post_run(
            '{"action_type":"final_answer","final_answer":"直接完成"}'
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["finalization_result"]["final_answer"], "直接完成")
        self.assertEqual([call.action.action_seq for call in self.action_committer.calls], [1])
        self.assertEqual(self.conversation.finished[0]["turn_id"], body["run_ref"]["turn_id"])
        self.assertNotIn("planner_input", body)
        self.assertNotIn("snapshots", body)

    def test_tool_call_rebuilds_context_and_keeps_full_rows_in_artifact(self) -> None:
        response = self.post_run(
            '{"action_type":"tool_call","tool_call":{"tool_name":"query_data","arguments":{"query":"查询销售额"}}}',
            '{"action_type":"final_answer","final_answer":"销售额查询完成"}',
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["iteration"], 1)
        self.assertEqual([call.action.action_seq for call in self.action_committer.calls], [1, 2])
        self.query.assert_awaited_once()
        self.assertEqual(len(self.artifacts.records), 1)
        self.assertIn("rows", next(iter(self.artifacts.records.values())).payload)
        self.assertEqual(len(self.llm.prompts), 2)
        self.assertIn("query_data 查询完成", self.llm.prompts[1])
        self.assertNotIn("sql_result", self.llm.prompts[1])

    def test_stream_query_exposes_progress_and_complete_result_contract(self) -> None:
        response = self.post_run_stream(
            '{"action_type":"tool_call","tool_call":{"tool_name":"query_data","arguments":{"query":"查询销售额"}}}',
            '{"action_type":"final_answer","final_answer":"销售额查询完成"}',
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        events = self.parse_sse_events(response)
        event_types = [event["event_type"] for event in events]
        self.assertIn("tool.started", event_types)
        self.assertIn("tool.progress", event_types)
        self.assertIn("tool.completed", event_types)
        self.assertIn("run.completed", event_types)
        self.assertIn("run.result", event_types)
        self.assertLess(event_types.index("run.completed"), event_types.index("run.result"))
        self.assertEqual(
            sum(
                event_type in {"run.completed", "run.failed", "run.timeout", "run.cancelled"}
                for event_type in event_types
            ),
            1,
        )
        run_ids = {event["run_ref"]["run_id"] for event in events}
        self.assertEqual(len(run_ids), 1)
        serialized = json.dumps(events, ensure_ascii=False)
        self.assertNotIn("SELECT amount FROM sales", serialized)
        self.assertNotIn("sql_result", serialized)
        self.assertNotIn('"rows"', serialized)
        self.assertNotIn('"prompt"', serialized)

    def test_stream_pause_and_resume_keep_same_run_identity(self) -> None:
        paused_response = self.post_run_stream(
            '{"action_type":"ask_user","ask_user":{"question":"使用哪个年份？","reason_code":"missing_condition","required_fields":["year"]}}'
        )
        self.assertEqual(paused_response.status_code, 200, paused_response.text)
        paused_events = self.parse_sse_events(paused_response)
        paused_types = [event["event_type"] for event in paused_events]
        self.assertIn("confirmation.required", paused_types)
        self.assertIn("run.result", paused_types)
        confirmation_event = next(
            event for event in paused_events if event["event_type"] == "confirmation.required"
        )
        run_ref = confirmation_event["run_ref"]
        confirmation_id = confirmation_event["payload"]["confirmation_id"]

        self.llm.set_responses(
            '{"action_type":"final_answer","final_answer":"已按 2026 年继续完成"}'
        )
        resumed_response = self.post_resume_stream(
            run_ref["run_id"],
            confirmation_id,
            resolved_conditions={"year": "2026"},
        )

        self.assertEqual(resumed_response.status_code, 200, resumed_response.text)
        resumed_events = self.parse_sse_events(resumed_response)
        resumed_types = [event["event_type"] for event in resumed_events]
        self.assertIn("confirmation.resolved", resumed_types)
        self.assertIn("run.completed", resumed_types)
        self.assertIn("run.result", resumed_types)
        self.assertEqual(
            {event["run_ref"]["run_id"] for event in resumed_events},
            {run_ref["run_id"]},
        )
        self.assertEqual(
            {event["run_ref"]["turn_id"] for event in resumed_events},
            {run_ref["turn_id"]},
        )

    def test_stream_operation_failure_emits_recoverable_stream_error(self) -> None:
        self.llm.set_responses(
            '{"action_type":"final_answer","final_answer":"不会执行"}'
        )
        with patch(
            "app.api.routers.harness._build_controller",
            side_effect=RuntimeError("internal failure details"),
        ):
            response = self.client.post(
                "/api/harness/run/stream",
                json={"input_text": "分析销售数据", "user_id": "user-1"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        events = self.parse_sse_events(response)
        self.assertEqual([event["event_type"] for event in events], ["stream.failed"])
        self.assertEqual(
            events[0]["payload"]["error_code"],
            "stream_operation_failed",
        )
        self.assertNotIn("internal failure details", response.text)

    def test_resume_passes_free_text_confirmation_to_next_planner(self) -> None:
        paused = self.post_run(
            '{"action_type":"ask_user","ask_user":{"question":"使用哪个销售额口径？","reason_code":"conflicting_definition"}}'
        ).json()
        self.llm.set_responses(
            '{"action_type":"final_answer","final_answer":"已按用户说明继续完成"}'
        )

        resumed = self.post_resume(
            paused["run_ref"]["run_id"],
            paused["confirmation"]["confirmation_id"],
            answer="使用财务销售额，不包含运费，并考虑退款。",
        )

        self.assertEqual(resumed.status_code, 200, resumed.text)
        self.assertIn("不包含运费", self.llm.prompts[-1])

    def test_invalid_planner_action_finishes_as_failed_run(self) -> None:
        response = self.post_run(
            '{"action_type":"tool_call","tool_call":{"tool_name":"unknown_tool","arguments":{}}}'
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "failed")
        self.assertEqual(self.action_committer.calls, [])
        self.query.assert_not_awaited()

    def test_unrecoverable_query_error_is_observed_before_final_answer(self) -> None:
        self.graph_error = RuntimeError("query failed")
        response = self.post_run(
            '{"action_type":"tool_call","tool_call":{"tool_name":"query_data","arguments":{"query":"查询销售额"}}}',
            '{"action_type":"final_answer","final_answer":"查询失败，无法给出数据结论"}',
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["finalization_result"]["final_answer"],
            "查询失败，无法给出数据结论",
        )
        self.assertEqual(self.query.await_count, 1)
        final_state = self.run_store.states[response.json()["run_ref"]["run_id"]]
        self.assertEqual(final_state["harness"]["observations"][0]["status"], "unrecoverable_error")

    def test_temporary_query_error_retries_same_action(self) -> None:
        self.graph_error = ConnectionError("database unavailable")
        response = self.post_run(
            '{"action_type":"tool_call","tool_call":{"tool_name":"query_data","arguments":{"query":"查询销售额"}}}',
            '{"action_type":"final_answer","final_answer":"数据源暂时不可用"}',
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.query.await_count, 3)
        self.assertEqual([call.action.action_seq for call in self.action_committer.calls], [1, 2])
        final_state = self.run_store.states[response.json()["run_ref"]["run_id"]]
        self.assertEqual(final_state["harness"]["observations"][0]["status"], "temporary_error")

    def test_query_timeout_is_not_replayed(self) -> None:
        self.graph_error = TimeoutError("query tool timed out")
        response = self.post_run(
            '{"action_type":"tool_call","tool_call":{"tool_name":"query_data","arguments":{"query":"查询销售额"}}}',
            '{"action_type":"final_answer","final_answer":"查询超时，无法给出数据结论"}',
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.query.await_count, 1)
        self.assertEqual(
            response.json()["finalization_result"]["final_answer"],
            "查询超时，无法给出数据结论",
        )

    def test_repeated_failed_tool_request_is_blocked_before_execution(self) -> None:
        self.graph_error = TimeoutError("query tool timed out")
        response = self.post_run(
            '{"action_type":"tool_call","tool_call":{"tool_name":"query_data","arguments":{"query":"查询销售额"}}}',
            '{"action_type":"tool_call","tool_call":{"tool_name":"query_data","arguments":{"query":"查询销售额"}}}',
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "failed")
        self.assertEqual(self.query.await_count, 1)
        self.assertEqual(len(self.action_committer.calls), 1)

    def test_ask_user_persists_waiting_state_and_status_endpoint(self) -> None:
        response = self.post_run(
            '{"action_type":"ask_user","ask_user":{"question":"使用哪个月份？","reason_code":"missing_condition","required_fields":["year"]}}'
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "waiting_confirmation")
        self.assertEqual(body["phase"], "wait_confirmation")
        run_id = body["run_ref"]["run_id"]
        confirmation_id = body["confirmation"]["confirmation_id"]
        self.assertEqual(self.run_store.states[run_id]["harness"]["status"], "waiting_confirmation")
        self.assertEqual(self.run_store.confirmations[confirmation_id]["status"], "pending")
        self.assertFalse(self.conversation.finished)

        status = self.client.get(f"/api/harness/run/status?run_id={run_id}&user_id=user-1")
        self.assertEqual(status.status_code, 200, status.text)
        self.assertEqual(status.json()["pending_confirmation"]["confirmation_id"], confirmation_id)
        self.assertEqual(status.json()["turn_id"] if "turn_id" in status.json() else status.json()["run_ref"]["turn_id"], body["run_ref"]["turn_id"])

    def test_resume_rebuilds_context_with_same_run_identity(self) -> None:
        paused = self.post_run(
            '{"action_type":"ask_user","ask_user":{"question":"使用哪个年份？","reason_code":"missing_condition","required_fields":["year"]}}'
        ).json()
        run_ref = paused["run_ref"]
        self.llm.set_responses(
            '{"action_type":"final_answer","final_answer":"已按 2026 年继续完成"}'
        )

        resumed = self.post_resume(
            run_ref["run_id"],
            paused["confirmation"]["confirmation_id"],
            resolved_conditions={"year": "2026"},
        )

        self.assertEqual(resumed.status_code, 200, resumed.text)
        body = resumed.json()
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["run_ref"], run_ref)
        self.assertEqual(self.run_store.states[run_ref["run_id"]]["turn_id"], run_ref["turn_id"])
        self.assertIn("2026", self.llm.prompts[-1])
        self.assertEqual([call.action.action_seq for call in self.action_committer.calls], [1, 2])

    def test_repeated_confirmation_is_stopped_after_reply_is_consumed(self) -> None:
        paused = self.post_run(
            '{"action_type":"ask_user","ask_user":{"question":"哪个年份？","reason_code":"missing_condition"}}'
        ).json()
        run_id = paused["run_ref"]["run_id"]
        confirmation_id = paused["confirmation"]["confirmation_id"]
        self.llm.set_responses(
            '{"action_type":"ask_user","ask_user":{"question":"哪个年份？","reason_code":"missing_condition"}}'
        )

        resumed = self.post_resume(
            run_id,
            confirmation_id,
            answer="我无法提供年份",
        )

        self.assertEqual(resumed.status_code, 200, resumed.text)
        body = resumed.json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(
            self.run_store.states[run_id]["harness"]["confirmation_attempt_count"],
            1,
        )
        self.assertEqual(len(self.run_store.confirmations), 1)

    def test_repeated_confirmation_is_idempotent_and_conflict_is_rejected(self) -> None:
        paused = self.post_run(
            '{"action_type":"ask_user","ask_user":{"question":"哪个年份？","reason_code":"missing_condition"}}'
        ).json()
        run_id = paused["run_ref"]["run_id"]
        confirmation_id = paused["confirmation"]["confirmation_id"]
        self.llm.set_responses('{"action_type":"final_answer","final_answer":"完成"}')
        first = self.post_resume(run_id, confirmation_id)
        self.assertEqual(first.status_code, 200, first.text)

        repeated = self.post_resume(run_id, confirmation_id)
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json()["resume_status"], "idempotent")
        self.assertEqual(len(self.action_committer.calls), 2)

        conflict = self.post_resume(run_id, confirmation_id, answer="2030 年")
        self.assertEqual(conflict.status_code, 409, conflict.text)

    def test_cross_user_resume_is_rejected(self) -> None:
        paused = self.post_run(
            '{"action_type":"ask_user","ask_user":{"question":"哪个年份？","reason_code":"missing_condition"}}'
        ).json()
        response = self.post_resume(
            paused["run_ref"]["run_id"],
            paused["confirmation"]["confirmation_id"],
            user_id="other-user",
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_completed_tool_is_not_reexecuted_after_pause_and_resume(self) -> None:
        paused = self.post_run(
            '{"action_type":"tool_call","tool_call":{"tool_name":"query_data","arguments":{"query":"查询销售额"}}}',
            '{"action_type":"ask_user","ask_user":{"question":"确认查询口径？","reason_code":"conflicting_definition"}}',
        ).json()
        self.assertEqual(self.query.await_count, 1)
        self.llm.set_responses('{"action_type":"final_answer","final_answer":"确认后完成"}')

        response = self.post_resume(
            paused["run_ref"]["run_id"],
            paused["confirmation"]["confirmation_id"],
        )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.query.await_count, 1)
        self.assertEqual([call.action.action_seq for call in self.action_committer.calls], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
