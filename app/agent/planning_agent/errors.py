"""切片 B Planner 统一错误。"""

from app.agent.state_result_store.contracts import ErrorCategory, RunError


class PlannerFailure(Exception):
    """Planner 失败只向控制器暴露统一的 RunError。"""

    def __init__(self, error: RunError) -> None:
        self.error = error
        super().__init__(error.message)


def planner_error(code: str, message: str, *, retryable: bool = False) -> PlannerFailure:
    return PlannerFailure(
        RunError(
            category=ErrorCategory.PLANNER,
            code=code,
            message=message,
            retryable=retryable,
        )
    )


__all__ = ["PlannerFailure", "planner_error"]
