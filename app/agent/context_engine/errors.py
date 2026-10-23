"""上下文构建中可明确识别的拒绝原因。"""


class ContextBudgetExceeded(ValueError):
    """必带上下文无法放入预算，不应将该轮输入作为后续历史。"""
