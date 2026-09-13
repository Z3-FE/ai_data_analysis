"""切片 B 的动作提交替身。

它只服务切片 B；未来接入真实 Checkpointer/业务记录时，在
LoopController(action_committer=...) 注入点替换为生产 ActionCommitter。
"""

from app.agent.loop_controller.action_commit import (
    ActionCommitRequest,
    ActionCommitResult,
)


class FakeActionCommitter:
    """记录 prepared、checkpoint、committed 三个可观察阶段。"""

    def __init__(self) -> None:
        self.calls: list[ActionCommitRequest] = []
        self.events: list[tuple[str, int]] = []

    async def commit(self, request: ActionCommitRequest) -> ActionCommitResult:
        self.calls.append(request)
        self.events.append(("prepared", request.action.action_seq))
        self.events.append(("checkpoint", request.action.action_seq))
        self.events.append(("committed", request.action.action_seq))
        action_id = request.action.tool_call.action_id if request.action.tool_call else None
        return ActionCommitResult(
            status="committed",
            action_seq=request.action.action_seq,
            action_type=request.action.action_type,
            action_id=action_id,
        )


__all__ = ["FakeActionCommitter"]
