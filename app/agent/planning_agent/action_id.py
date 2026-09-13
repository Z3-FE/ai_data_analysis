"""切片 B 正式动作 ID 生成。"""

from app.agent.planning_agent.contracts import ActionIdFactory


class SequentialActionIdFactory(ActionIdFactory):
    """用运行身份、迭代和序号生成可审计的动作 ID。"""

    def issue(self, *, run_id: str, iteration: int, action_seq: int) -> str:
        return f"{run_id}:i{iteration}:a{action_seq}"


__all__ = ["SequentialActionIdFactory"]
