"""切片 A 的最小收口替身。

该替身服务切片 A 至 D，在切片 E 被替换为
``app.agent.finalization.service.py::FinalizationService``；替换点是
``LoopController(finalization_service=...)`` 的依赖注入参数。它不写历史、
不保存终态 Checkpoint、不释放 active_run，也不调用 Memory Formation。
"""

from app.agent.state_result_store.contracts import HarnessStatus
from app.agent.loop_controller.contracts import FinalizationInput, FinalizationResult


class FakeFinalizationService:
    """只回传受控完成结果，由 LoopController 包装成 LoopRunResult。"""

    def __init__(self) -> None:
        self.calls: list[FinalizationInput] = []

    async def finalize(self, value: FinalizationInput) -> FinalizationResult:
        self.calls.append(value)
        return FinalizationResult(
            run_ref=value.run_ref,
            status=value.terminal_status,
            final_answer=value.final_answer,
        )


__all__ = ["FakeFinalizationService"]
