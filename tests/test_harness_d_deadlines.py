"""Harness D 的运行级 deadline、取消和恢复边界。"""

from __future__ import annotations

import asyncio
import copy
import unittest
from datetime import UTC, datetime, timedelta

from app.agent.loop_controller.contracts import ConfirmationResolution, StartRunCommand
from app.agent.loop_controller.controller import LoopController
from app.agent.planning_agent.contracts import ActionIssuanceContext
from app.agent.state_result_store.contracts import (
    ActionType,
    AskUserRequest,
    HarnessRunRef,
    HarnessStatus,
    LoopPhase,
    NextAction,
    ResultStatus,
    ToolCall,
    ToolResult,
    ToolSpec,
)
from app.agent.tool_runtime.contracts import ToolExecutionRequest
from tests.fakes.slice_a.fake_finalization import FakeFinalizationService
from tests.fakes.slice_a.fake_run_store import FakeRunStore
from tests.fakes.slice_b.fake_action_committer import FakeActionCommitter
from tests.test_context_engine import FakeMemoryReader, _engine


def _run_ref(run_id: str) -> HarnessRunRef:
    return HarnessRunRef(
        user_id="user-1",
        conversation_id="conversation-1",
        thread_id="thread-1",
        turn_id=f"{run_id}:turn",
        run_id=run_id,
    )


class DeadlineRunStore(FakeRunStore):
    """在创建现场时注入短 deadline，用于确定性触发运行级超时。"""

    def __init__(self, deadline_seconds: float) -> None:
        super().__init__()
        self.deadline_seconds = deadline_seconds

    async def create(self, run_ref, state) -> None:
        state["harness"]["deadline_at"] = (
            datetime.now(UTC) + timedelta(seconds=self.deadline_seconds)
        ).isoformat()
        await super().create(run_ref, state)


class SlowContextBuilder:
    def __init__(self, inner, delay: float) -> None:
        self.inner = inner
        self.delay = delay

    async def build(self, request):
        await asyncio.sleep(self.delay)
        return await self.inner.build(request)


class ScriptedPlanner:
    def __init__(self, action_factory) -> None:
        self.action_factory = action_factory
        self.started = asyncio.Event()
        self.calls = 0

    async def plan(self, value, *, issuance: ActionIssuanceContext) -> NextAction:
        self.calls += 1
        self.started.set()
        return self.action_factory(issuance)


class BlockingPlanner(ScriptedPlanner):
    def __init__(self) -> None:
        super().__init__(
            lambda issuance: NextAction(
                action_seq=issuance.action_seq,
                action_type=ActionType.FINAL_ANSWER,
                final_answer="不会执行到这里",
            )
        )
        self.release = asyncio.Event()

    async def plan(self, value, *, issuance: ActionIssuanceContext) -> NextAction:
        self.started.set()
        await self.release.wait()
        return await super().plan(value, issuance=issuance)


class SlowActionCommitter(FakeActionCommitter):
    async def commit(self, request):
        await asyncio.sleep(0.2)
        return await super().commit(request)


class SlowToolRuntime:
    async def execute(self, request: ToolExecutionRequest) -> ToolResult:
        await asyncio.sleep(0.2)
        now = datetime.now(UTC)
        return ToolResult(
            tool_call_id=request.tool_call.action_id,
            tool_name=request.tool_call.tool_name,
            status=ResultStatus.SUCCESS,
            summary="不会返回",
            started_at=now,
            finished_at=now,
            duration_ms=0,
        )


class ConfirmationRunStore(DeadlineRunStore):
    async def pause_for_confirmation(self, run_ref, state, record) -> None:
        self.states[run_ref.run_id] = copy.deepcopy(state)
        self.snapshots.append(copy.deepcopy(state))

    async def resolve_confirmation(self, run_ref, reply) -> ConfirmationResolution:
        state = copy.deepcopy(self.states[run_ref.run_id])
        state["harness"]["status"] = HarnessStatus.RUNNING.value
        state["harness"]["phase"] = LoopPhase.RESTORE_RUN.value
        state["harness"]["pending_confirmation"] = None
        self.states[run_ref.run_id] = copy.deepcopy(state)
        return ConfirmationResolution(status="confirmed", state=state)


def _final_answer(issuance: ActionIssuanceContext) -> NextAction:
    return NextAction(
        action_seq=issuance.action_seq,
        action_type=ActionType.FINAL_ANSWER,
        final_answer="完成",
    )


def _tool_call(issuance: ActionIssuanceContext) -> NextAction:
    return NextAction(
        action_seq=issuance.action_seq,
        action_type=ActionType.TOOL_CALL,
        tool_call=ToolCall(
            action_id=f"run:i{issuance.iteration}:a{issuance.action_seq}",
            tool_name="query_data",
            arguments={"query": "查询销售额"},
        ),
    )


def _ask_user(issuance: ActionIssuanceContext) -> NextAction:
    return NextAction(
        action_seq=issuance.action_seq,
        action_type=ActionType.ASK_USER,
        ask_user=AskUserRequest(
            question="使用哪个口径？",
            reason_code="conflicting_definition",
        ),
    )


class HarnessDeadlineTest(unittest.IsolatedAsyncioTestCase):
    def _controller(self, *, planner, store, context_builder=None, **kwargs):
        context_engine, _, _ = _engine(FakeMemoryReader())
        return LoopController(
            context_builder=context_builder or context_engine,
            planning_agent=planner,
            finalization_service=FakeFinalizationService(run_store=store),
            run_store=store,
            run_timeout_seconds=1,
            **kwargs,
        )

    async def test_context_timeout_is_finalized_as_timeout(self) -> None:
        context_engine, _, _ = _engine(FakeMemoryReader())
        store = DeadlineRunStore(0.03)
        controller = self._controller(
            planner=ScriptedPlanner(_final_answer),
            store=store,
            context_builder=SlowContextBuilder(context_engine, 0.2),
        )

        result = await controller.start(
            StartRunCommand(run_ref=_run_ref("context-timeout"), input_text="查询销售额")
        )

        self.assertEqual(result.status, HarnessStatus.TIMEOUT)
        self.assertEqual(
            store.states["context-timeout"]["harness"]["status"],
            HarnessStatus.TIMEOUT.value,
        )
        self.assertEqual(
            store.states["context-timeout"]["harness"]["last_error"]["code"],
            "run_deadline_exceeded",
        )

    async def test_action_commit_timeout_is_finalized_as_timeout(self) -> None:
        store = DeadlineRunStore(0.03)
        controller = self._controller(
            planner=ScriptedPlanner(_final_answer),
            store=store,
            action_committer=SlowActionCommitter(),
        )

        result = await controller.start(
            StartRunCommand(run_ref=_run_ref("commit-timeout"), input_text="查询销售额")
        )

        self.assertEqual(result.status, HarnessStatus.TIMEOUT)
        self.assertEqual(
            store.states["commit-timeout"]["harness"]["status"],
            HarnessStatus.TIMEOUT.value,
        )

    async def test_tool_timeout_is_finalized_as_timeout(self) -> None:
        store = DeadlineRunStore(0.03)
        spec = ToolSpec(
            name="query_data",
            description="查询数据",
            permission="data.query.read",
        )
        controller = self._controller(
            planner=ScriptedPlanner(_tool_call),
            store=store,
            action_committer=FakeActionCommitter(),
            tool_runtime=SlowToolRuntime(),
            tool_specs=(spec,),
        )

        result = await controller.start(
            StartRunCommand(run_ref=_run_ref("tool-timeout"), input_text="查询销售额")
        )

        self.assertEqual(result.status, HarnessStatus.TIMEOUT)
        self.assertEqual(
            store.states["tool-timeout"]["harness"]["status"],
            HarnessStatus.TIMEOUT.value,
        )

    async def test_cancelled_request_is_finalized_as_cancelled(self) -> None:
        store = FakeRunStore()
        planner = BlockingPlanner()
        controller = self._controller(planner=planner, store=store)
        task = asyncio.create_task(
            controller.start(
                StartRunCommand(run_ref=_run_ref("cancelled"), input_text="查询销售额")
            )
        )
        await asyncio.wait_for(planner.started.wait(), timeout=1)
        task.cancel()

        result = await task

        self.assertEqual(result.status, HarnessStatus.CANCELLED)
        self.assertEqual(
            store.states["cancelled"]["harness"]["status"],
            HarnessStatus.CANCELLED.value,
        )
        self.assertEqual(
            store.states["cancelled"]["harness"]["last_error"]["code"],
            "run_cancelled",
        )

    async def test_expired_resume_does_not_call_planner(self) -> None:
        store = ConfirmationRunStore(60)
        first_planner = ScriptedPlanner(_ask_user)
        controller = self._controller(
            planner=first_planner,
            store=store,
            action_committer=FakeActionCommitter(),
        )
        run_ref = _run_ref("expired-resume")

        paused = await controller.start(
            StartRunCommand(run_ref=run_ref, input_text="查询销售额")
        )
        self.assertEqual(paused.status, HarnessStatus.WAITING_CONFIRMATION)
        started_at = datetime.now(UTC) - timedelta(seconds=2)
        store.states[run_ref.run_id]["harness"]["started_at"] = started_at.isoformat()
        store.states[run_ref.run_id]["harness"]["deadline_at"] = (
            started_at + timedelta(seconds=1)
        ).isoformat()

        resumed = await controller.resume(
            type("Resume", (), {"run_ref": run_ref, "reply": object()})()
        )

        self.assertEqual(resumed.status, HarnessStatus.TIMEOUT)
        self.assertEqual(first_planner.calls, 1)


if __name__ == "__main__":
    unittest.main()
