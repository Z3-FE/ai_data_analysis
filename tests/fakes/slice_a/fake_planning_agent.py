"""切片 A 专用 Planner 替身。

该替身只服务切片 A，在切片 B 被替换为
``app.agent.planning_agent.planning_agent.py::PlanningAgent``；替换点是
``LoopController(planning_agent=...)`` 的依赖注入参数。它不执行工具，
也不模拟正式动作提交。
"""

from app.agent.planning_agent.contracts import ActionIssuanceContext
from app.agent.state_result_store.contracts import ActionType, NextAction, PlannerInput


class FakePlanningAgent:
    """始终生成一个可直接收口的 final_answer 动作。"""

    def __init__(self, final_answer: str = "切片 A 已完成。") -> None:
        self.final_answer = final_answer
        self.calls: list[PlannerInput] = []

    async def plan(
        self,
        value: PlannerInput,
        *,
        issuance: ActionIssuanceContext,
    ) -> NextAction:
        self.calls.append(value)
        return NextAction(
            action_seq=issuance.action_seq,
            action_type=ActionType.FINAL_ANSWER,
            final_answer=self.final_answer,
            rationale_summary="切片 A 固定收口",
        )


__all__ = ["FakePlanningAgent"]
