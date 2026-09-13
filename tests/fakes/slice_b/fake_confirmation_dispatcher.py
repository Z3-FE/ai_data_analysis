"""切片 B 的确认分派替身。

该替身只服务切片 B；切片 D 会在 LoopController 的
``confirmation_dispatcher=...`` 注入点替换为真实 ConfirmationStore 和暂停恢复实现。
它立即返回固定答案，不创建持久确认记录，也不改变 Harness 状态。
"""

from app.agent.loop_controller.contracts import ConfirmationDispatcher
from app.agent.state_result_store.contracts import NextAction


class FakeConfirmationDispatcher:
    """记录 ask_user 动作并立即返回固定确认结果。"""

    def __init__(self) -> None:
        self.calls: list[NextAction] = []

    async def dispatch(self, value: NextAction) -> str:
        self.calls.append(value)
        return "用户确认完成。"


__all__ = ["FakeConfirmationDispatcher"]
