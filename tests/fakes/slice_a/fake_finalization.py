"""切片 A 的最小收口替身。

该替身服务切片 A 至 D，在切片 E 被替换为
``app.agent.finalization.service.PostgresFinalizationService``；替换点是
``LoopController(finalization_service=...)`` 的依赖注入参数。它记录收口输入，
并像真实服务一样把运行现场推进到终态；不写历史、不释放 active_run，
也不调用 Memory Formation。
"""

from app.agent.loop_controller.contracts import FinalizationInput, FinalizationResult
from app.agent.state_result_store.contracts import (
    HarnessStateSnapshot,
    LoopPhaseStatusType,
)
from app.agent.state_result_store.state import transition_harness_state
from tests.fakes.slice_a.fake_run_store import FakeRunStore


class FakeFinalizationService:
    """记录收口输入，写入终态 checkpoint，并回传受控完成结果。"""

    def __init__(self, run_store: FakeRunStore | None = None) -> None:
        self.calls: list[FinalizationInput] = []
        self.run_store = run_store

    async def finalize(self, value: FinalizationInput) -> FinalizationResult:
        self.calls.append(value)
        if self.run_store is not None:
            state = await self.run_store.load(value.run_ref)
            state["harness"] = transition_harness_state(
                state["harness"],
                status=value.terminal_status,
                phase=LoopPhaseStatusType.FINALIZATION,
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
        return FinalizationResult(
            run_ref=value.run_ref,
            status=value.terminal_status,
            final_answer=value.final_answer,
        )


__all__ = ["FakeFinalizationService"]
