"""Finalization 的对外错误契约。"""

from __future__ import annotations

from app.agent.state_result_store.contracts import HarnessRunRef


class FinalizationFailure(Exception):
    """收口在账本某阶段中断；运行保持可恢复现场，由 reconcile 继续。"""

    def __init__(self, run_ref: HarnessRunRef, stage: str) -> None:
        super().__init__(f"收口在 {stage} 阶段中断: run_id={run_ref.run_id}")
        self.run_ref = run_ref
        self.stage = stage


__all__ = ["FinalizationFailure"]
