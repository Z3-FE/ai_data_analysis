"""切片 D 的进程内确认存储。"""

from __future__ import annotations

from copy import deepcopy

from app.agent.state_result_store.contracts import (
    ConfirmationRecord,
    HarnessRunRef,
)


class InMemoryConfirmationStore:
    """D 阶段确认存储。

    这是可替换的基础设施实现：E 之前保持进程内生命周期；生产环境替换点是
    LoopController 的 ``confirmation_store`` 注入，不改变 ConfirmationRecord 契约。
    """

    def __init__(self) -> None:
        self.records: dict[str, ConfirmationRecord] = {}

    async def create(self, record: ConfirmationRecord) -> None:
        key = self._key(record.run_ref)
        if key in self.records:
            raise ValueError(f"确认记录已存在: {key}")
        self.records[key] = deepcopy(record)

    async def load(self, run_ref: HarnessRunRef) -> ConfirmationRecord:
        key = self._key(run_ref)
        try:
            return deepcopy(self.records[key])
        except KeyError as exc:
            raise ValueError(f"确认记录不存在: {key}") from exc

    async def save(self, record: ConfirmationRecord) -> None:
        key = self._key(record.run_ref)
        if key not in self.records:
            raise ValueError(f"确认记录不存在: {key}")
        self.records[key] = deepcopy(record)

    @staticmethod
    def _key(run_ref: HarnessRunRef) -> str:
        return run_ref.run_id


__all__ = ["InMemoryConfirmationStore"]
