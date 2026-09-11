"""切片 A 的最小 Harness 主循环。

只编排 START_RUN -> BUILD_CONTEXT -> PLAN -> FINALIZATION。真实 ContextEngine
通过依赖注入使用；Planning、Finalization 和持久化均由调用方提供替身或实现。
"""

from __future__ import annotations

from app.agent.harness.context_service import HarnessContextRequestFactory
from app.agent.harness.contracts import (
    ActionType,
    HarnessStatus,
    LoopPhase,
    PlannerInput,
    PlannerStateView,
)
from app.agent.harness.loop_contracts import (
    ContextBuilder,
    FinalizationInput,
    FinalizationPort,
    HarnessRunStore,
    LoopRunResult,
    PlanningPort,
    StartRunCommand,
)
from app.agent.harness.state import (
    new_harness_control_state,
    transition_harness_state,
)
from app.agent.state import HarnessGraphState


class LoopController:
    """切片 A 的异步闭环控制器；start 等到收口后返回。"""

    def __init__(
        self,
        *,
        context_builder: ContextBuilder,
        planning_agent: PlanningPort,
        finalization_service: FinalizationPort,
        run_store: HarnessRunStore,
        context_request_factory: HarnessContextRequestFactory | None = None,
        system_instructions: str = "你是一个数据分析助手。",
    ) -> None:
        self.context_builder = context_builder
        self.planning_agent = planning_agent
        self.finalization_service = finalization_service
        self.run_store = run_store
        self.context_request_factory = (
            context_request_factory or HarnessContextRequestFactory()
        )
        self.system_instructions = system_instructions

    async def start(self, command: StartRunCommand) -> LoopRunResult:
        state = self._new_state(command)
        await self.run_store.create(command.run_ref, state)

        state = self._transition(state, status=HarnessStatus.RUNNING, phase=LoopPhase.BUILD_CONTEXT)
        await self.run_store.save(command.run_ref, state)

        request = self.context_request_factory.create(
            state,
            system_instructions=self.system_instructions,
            agent_type="data_agent",
        )
        compiled_context = await self.context_builder.build(request)
        state["harness"]["last_context_build_id"] = compiled_context.build_id
        state["harness"]["last_context_token_count"] = compiled_context.token_count

        state = self._transition(state, status=HarnessStatus.RUNNING, phase=LoopPhase.PLAN)
        await self.run_store.save(command.run_ref, state)
        planner_input = PlannerInput(
            compiled_context=compiled_context,
            state_view=PlannerStateView(
                original_goal=state["harness"]["original_goal"],
                iteration=state["harness"]["iteration"],
            ),
            tool_specs=(),
        )
        action = await self.planning_agent.plan(planner_input)
        if action.action_type is not ActionType.FINAL_ANSWER or not action.final_answer:
            raise ValueError("切片 A 的 FakePlanningAgent 必须返回 final_answer")

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
                final_answer=action.final_answer,
            )
        )
        if finalization.status is not HarnessStatus.COMPLETED:
            raise ValueError("切片 A 的 Finalization 必须返回 completed")
        if finalization.run_ref != command.run_ref:
            raise ValueError("Finalization 返回的 run_ref 与当前运行不一致")

        state = self._transition(
            state,
            status=HarnessStatus.COMPLETED,
            phase=LoopPhase.FINALIZATION,
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
