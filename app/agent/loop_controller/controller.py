"""Harness 主循环的切片 A/B/C 最小编排。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.agent.context_engine.harness_context import HarnessContextRequestFactory
from app.agent.loop_controller.action_commit import ActionCommitRequest, ActionCommitter
from app.agent.loop_controller.contracts import (
    ConfirmationDispatcher,
    ContextBuilder,
    FinalizationInput,
    FinalizationPort,
    HarnessRunStore,
    LoopRunResult,
    PlanningPort,
    StartRunCommand,
    ToolRuntimePort,
)
from app.agent.planning_agent.contracts import ActionIssuanceContext
from app.agent.state import HarnessGraphState
from app.agent.state_result_store.contracts import (
    ActionType,
    HarnessStatus,
    LoopPhase,
    PlannerInput,
    PlannerStateView,
    ResultStatus,
    RunExecutionFence,
    RunObservation,
    ToolSpec,
)
from app.agent.state_result_store.state import (
    new_harness_control_state,
    transition_harness_state,
)
from app.agent.tool_runtime.contracts import ToolExecutionRequest


class LoopController:
    """异步运行控制器；C 在每次工具成功后重建 Context 再次规划。"""

    def __init__(
        self,
        *,
        context_builder: ContextBuilder,
        planning_agent: PlanningPort,
        finalization_service: FinalizationPort,
        run_store: HarnessRunStore,
        context_request_factory: HarnessContextRequestFactory | None = None,
        system_instructions: str = "你是一个数据分析助手。",
        action_committer: ActionCommitter | None = None,
        tool_runtime: ToolRuntimePort | None = None,
        confirmation_dispatcher: ConfirmationDispatcher | None = None,
        tool_specs: tuple[ToolSpec, ...] = (),
        continue_after_tool: bool = False,
    ) -> None:
        self.context_builder = context_builder
        self.planning_agent = planning_agent
        self.finalization_service = finalization_service
        self.run_store = run_store
        self.context_request_factory = (
            context_request_factory or HarnessContextRequestFactory()
        )
        self.system_instructions = system_instructions
        self.action_committer = action_committer
        self.tool_runtime = tool_runtime
        self.confirmation_dispatcher = confirmation_dispatcher
        self.tool_specs = tool_specs
        self.continue_after_tool = continue_after_tool

    async def start(self, command: StartRunCommand) -> LoopRunResult:
        state = self._new_state(command)
        await self.run_store.create(command.run_ref, state)
        state = self._transition(
            state, status=HarnessStatus.RUNNING, phase=LoopPhase.BUILD_CONTEXT
        )
        await self.run_store.save(command.run_ref, state)
        compiled_context = await self._build_context(state)
        state = self._transition(
            state, status=HarnessStatus.RUNNING, phase=LoopPhase.PLAN
        )
        await self.run_store.save(command.run_ref, state)
        planner_input = self._planner_input(state, compiled_context)

        if self.action_committer is None:
            action = await self.planning_agent.plan(planner_input)
            if action.action_type is not ActionType.FINAL_ANSWER or not action.final_answer:
                raise ValueError("切片 A 的 FakePlanningAgent 必须返回 final_answer")
            return await self._finalize(
                command=command,
                state=state,
                compiled_context=compiled_context,
                final_answer=action.final_answer,
            )

        if self.continue_after_tool:
            return await self._run_slice_c(
                command=command,
                state=state,
                compiled_context=compiled_context,
                planner_input=planner_input,
            )
        return await self._run_slice_b(
            command=command,
            state=state,
            compiled_context=compiled_context,
            planner_input=planner_input,
        )

    async def _run_slice_b(
        self,
        *,
        command: StartRunCommand,
        state: HarnessGraphState,
        compiled_context,
        planner_input: PlannerInput,
    ) -> LoopRunResult:
        """执行一次 B 动作；不重建 Context，也不实现暂停恢复。"""
        if self.tool_runtime is None or self.confirmation_dispatcher is None:
            raise ValueError("切片 B 必须注入 tool_runtime 和 confirmation_dispatcher")
        state = self._transition(
            state, status=HarnessStatus.RUNNING, phase=LoopPhase.VALIDATE_ACTION
        )
        await self.run_store.save(command.run_ref, state)
        action = await self._plan_action(command, state, planner_input)
        await self._commit_action(command, state, action)

        if action.action_type is ActionType.TOOL_CALL:
            assert action.tool_call is not None
            state = self._transition(
                state, status=HarnessStatus.RUNNING, phase=LoopPhase.EXECUTE_TOOL
            )
            await self.run_store.save(command.run_ref, state)
            tool_result = await self.tool_runtime.execute(
                ToolExecutionRequest(
                    run_ref=command.run_ref,
                    tool_call=action.tool_call,
                    action_seq=action.action_seq,
                )
            )
            final_answer = tool_result.summary
        elif action.action_type is ActionType.ASK_USER:
            final_answer = await self.confirmation_dispatcher.dispatch(action)
        else:
            final_answer = action.final_answer
        return await self._finalize(
            command=command,
            state=state,
            compiled_context=compiled_context,
            final_answer=final_answer,
        )

    async def _run_slice_c(
        self,
        *,
        command: StartRunCommand,
        state: HarnessGraphState,
        compiled_context,
        planner_input: PlannerInput,
    ) -> LoopRunResult:
        """执行 Tool -> Observation -> Context -> Planner 循环。"""
        if self.action_committer is None or self.tool_runtime is None:
            raise ValueError("切片 C 必须注入 action_committer 和 tool_runtime")
        while True:
            state = self._transition(
                state, status=HarnessStatus.RUNNING, phase=LoopPhase.VALIDATE_ACTION
            )
            await self.run_store.save(command.run_ref, state)
            action = await self._plan_action(command, state, planner_input)
            await self._commit_action(command, state, action)
            if action.action_type is ActionType.FINAL_ANSWER:
                return await self._finalize(
                    command=command,
                    state=state,
                    compiled_context=compiled_context,
                    final_answer=action.final_answer,
                )
            if action.action_type is not ActionType.TOOL_CALL or action.tool_call is None:
                raise ValueError("切片 C 只支持 tool_call 和 final_answer")

            state = self._transition(
                state, status=HarnessStatus.RUNNING, phase=LoopPhase.EXECUTE_TOOL
            )
            await self.run_store.save(command.run_ref, state)
            tool_result = await self.tool_runtime.execute(
                ToolExecutionRequest(
                    run_ref=command.run_ref,
                    tool_call=action.tool_call,
                    action_seq=action.action_seq,
                )
            )
            if tool_result.status not in {ResultStatus.SUCCESS, ResultStatus.PARTIAL}:
                raise ValueError("切片 C 只允许 success/partial 工具结果进入下一轮")

            state = self._transition(
                state, status=HarnessStatus.RUNNING, phase=LoopPhase.HANDLE_TOOL_RESULT
            )
            await self.run_store.save(command.run_ref, state)
            observation = RunObservation(
                observation_id=(
                    f"{command.run_ref.run_id}:o"
                    f"{len(state['harness']['observations']) + 1}"
                ),
                action_id=action.tool_call.action_id,
                tool_name=tool_result.tool_name,
                status=tool_result.status,
                summary=tool_result.summary,
                result_ref=tool_result.result_ref,
                evidence_refs=tool_result.evidence_refs,
                limitations=tool_result.limitations,
            )
            state["harness"]["observations"].append(observation.model_dump(mode="json"))
            state = self._transition(
                state, status=HarnessStatus.RUNNING, phase=LoopPhase.RECORD_OBSERVATION
            )
            state["harness"]["iteration"] += 1
            await self.run_store.save(command.run_ref, state)
            if state["harness"]["iteration"] >= state["harness"]["max_iterations"]:
                raise ValueError("Harness 达到最大迭代次数")

            state = self._transition(
                state, status=HarnessStatus.RUNNING, phase=LoopPhase.BUILD_CONTEXT
            )
            await self.run_store.save(command.run_ref, state)
            compiled_context = await self._build_context(state)
            state = self._transition(
                state, status=HarnessStatus.RUNNING, phase=LoopPhase.PLAN
            )
            await self.run_store.save(command.run_ref, state)
            planner_input = self._planner_input(state, compiled_context)

    async def _plan_action(
        self,
        command: StartRunCommand,
        state: HarnessGraphState,
        planner_input: PlannerInput,
    ):
        return await self.planning_agent.plan(
            planner_input,
            issuance=ActionIssuanceContext(
                run_id=command.run_ref.run_id,
                iteration=state["harness"]["iteration"],
                action_seq=state["harness"]["action_seq"] + 1,
            ),
        )

    async def _commit_action(self, command: StartRunCommand, state, action) -> None:
        expected_action_seq = state["harness"]["action_seq"] + 1
        if action.action_seq != expected_action_seq:
            raise ValueError("Planner 返回的 action_seq 不符合服务端发行序号")
        commit = await self.action_committer.commit(
            ActionCommitRequest(
                run_ref=command.run_ref,
                action=action,
                expected_action_seq=expected_action_seq,
                expected_state_version=state["harness"]["state_version"],
                expected_checkpoint_revision=state["harness"]["checkpoint_revision"],
                execution_fence=RunExecutionFence(
                    owner_id="loop-controller",
                    fencing_token=max(1, state["harness"]["fencing_token"]),
                    lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                ),
            )
        )
        if commit.status not in {"committed", "idempotent"}:
            raise ValueError("动作未提交，禁止进入下游")
        if commit.action_seq != action.action_seq:
            raise ValueError("提交结果的 action_seq 与动作不一致")
        state["harness"]["action_seq"] = action.action_seq

    async def _build_context(self, state):
        request = self.context_request_factory.create(
            state,
            system_instructions=self.system_instructions,
            agent_type="data_agent",
        )
        compiled_context = await self.context_builder.build(request)
        state["harness"]["last_context_build_id"] = compiled_context.build_id
        state["harness"]["last_context_token_count"] = compiled_context.token_count
        return compiled_context

    def _planner_input(self, state, compiled_context) -> PlannerInput:
        harness = state["harness"]
        return PlannerInput(
            compiled_context=compiled_context,
            state_view=PlannerStateView(
                original_goal=harness["original_goal"],
                iteration=harness["iteration"],
                observations=[
                    RunObservation.model_validate(value)
                    for value in harness["observations"]
                ],
                last_error=(
                    None
                    if harness.get("last_error") is None
                    else harness["last_error"]
                ),
            ),
            tool_specs=self.tool_specs,
        )

    async def _finalize(
        self,
        *,
        command: StartRunCommand,
        state: HarnessGraphState,
        compiled_context,
        final_answer: str | None,
    ) -> LoopRunResult:
        if not final_answer:
            raise ValueError("收口答案不能为空")
        state = self._transition(
            state,
            status=HarnessStatus.RUNNING,
            phase=LoopPhase.FINALIZATION,
            terminal_intent="completed",
        )
        await self.run_store.save(command.run_ref, state)
        finalization = await self.finalization_service.finalize(
            FinalizationInput(
                run_ref=command.run_ref,
                user_query=command.input_text,
                compiled_context=compiled_context,
                final_answer=final_answer,
            )
        )
        if finalization.status is not HarnessStatus.COMPLETED:
            raise ValueError("当前切片的 Finalization 必须返回 completed")
        if finalization.run_ref != command.run_ref:
            raise ValueError("Finalization 返回的 run_ref 与当前运行不一致")
        state = self._transition(
            state, status=HarnessStatus.COMPLETED, phase=LoopPhase.FINALIZATION
        )
        await self.run_store.save(command.run_ref, state)
        return LoopRunResult(
            run_ref=command.run_ref,
            status=finalization.status,
            phase=LoopPhase.FINALIZATION,
            iteration=state["harness"]["iteration"],
            finalization_result=finalization,
        )

    @staticmethod
    def _new_state(command: StartRunCommand) -> HarnessGraphState:
        harness = new_harness_control_state(original_goal=command.input_text)
        return {
            "input_text": command.input_text,
            "original_question": command.input_text,
            "user_id": command.run_ref.user_id,
            "conversation_id": command.run_ref.conversation_id,
            "thread_id": command.run_ref.thread_id,
            "turn_id": command.run_ref.turn_id,
            "run_id": command.run_ref.run_id,
            "project_id": command.project_id,
            "asset_ids": list(command.asset_ids),
            "harness": harness,
        }

    @staticmethod
    def _transition(
        state: HarnessGraphState,
        *,
        status: HarnessStatus,
        phase: LoopPhase,
        terminal_intent: str | None = None,
    ) -> HarnessGraphState:
        state["harness"] = transition_harness_state(
            state["harness"],
            status=status,
            phase=phase,
            terminal_intent=terminal_intent,
        )
        return state


__all__ = ["LoopController"]
