"""本轮上下文候选压缩。"""

from app.agent.context_engine.contracts import ContextItem
from app.agent.context_engine.interfaces import TokenCounter


class TokenBoundaryCompressor:
    """按模型 token 边界缩短单个候选，并显式标明截断。"""

    def __init__(self, token_counter: TokenCounter) -> None:
        self.token_counter = token_counter

    async def compress(self, item: ContextItem, *, max_tokens: int) -> str:
        """只影响本轮输入，不更新会话摘要或长期记忆正文。"""
        return self.token_counter.truncate(
            item.content,
            max_tokens,
            marker="\n[内容已按本轮上下文预算截断]",
        )


__all__ = ["TokenBoundaryCompressor"]
