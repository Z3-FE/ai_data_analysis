"""切片 A 的进程内运行状态替身。

该替身只服务切片 A，在切片 D/E 的持久化恢复实现完成后被替换为
``app.agent.state_result_store.run_store.py::PostgresHarnessRunStore``；替换点是
``LoopController(run_store=...)`` 的依赖注入参数。它只保存测试进程内的
HarnessRunState，不代表生产 Checkpointer 或数据库事务。
"""

from copy import deepcopy

from app.agent.state_result_store.contracts import HarnessRunRef
from app.agent.state import HarnessRunState


class FakeRunStore:
    """记录创建和保存顺序，并返回隔离的状态副本。"""

    def __init__(self) -> None:
        self.states: dict[str, HarnessRunState] = {}
        self.create_calls: list[str] = []
        self.save_calls: list[str] = []
        self.snapshots: list[HarnessRunState] = []

    async def create(self, run_ref: HarnessRunRef, state: HarnessRunState) -> None:
        key = run_ref.run_id
        if key in self.states:
            raise ValueError(f"run 已存在: {key}")
        self.states[key] = deepcopy(state)
        self.create_calls.append(key)
        self.snapshots.append(deepcopy(state))

    async def save(self, run_ref: HarnessRunRef, state: HarnessRunState) -> None:
        key = run_ref.run_id
        if key not in self.states:
            raise ValueError(f"run 不存在: {key}")
        self.states[key] = deepcopy(state)
        self.save_calls.append(key)
        self.snapshots.append(deepcopy(state))

    async def load(self, run_ref: HarnessRunRef) -> HarnessRunState:
        try:
            return deepcopy(self.states[run_ref.run_id])
        except KeyError as exc:
            raise ValueError(f"run 不存在: {run_ref.run_id}") from exc


__all__ = ["FakeRunStore"]
