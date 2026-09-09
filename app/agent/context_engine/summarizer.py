"""较早会话的增量摘要实现。"""

from collections.abc import Sequence
from typing import Any

from app.agent.context_engine.interfaces import TokenCounter
from app.agent.memory.interfaces import MemoryRecord


def _history_text(messages: Sequence[MemoryRecord]) -> str:
    """把 Working 记录转换成可审计的角色文本。"""
    lines = []
    for message in messages:
        role = str(message.structured_data.get("role") or "unknown")
        lines.append(f"{role}: {message.content}")
    return "\n".join(lines)


class DeterministicConversationSummarizer:
    """无 LLM 或模型失败时保留原始语义的确定性回退。"""

    def __init__(self, token_counter: TokenCounter) -> None:
        self.token_counter = token_counter

    async def summarize(
        self,
        *,
        previous_summary: str,
        messages: Sequence[MemoryRecord],
        max_tokens: int,
    ) -> str:
        history = _history_text(messages)
        previous = previous_summary.strip()
        if not previous:
            return self.token_counter.truncate(
                history,
                max_tokens,
                marker="\n[较早会话摘要已达到预算上限]",
            )
        # 回退摘要同时为旧语义和新增历史分配空间，避免只保留旧摘要前缀。
        labels = "已有会话摘要：\n\n\n后续会话记录：\n"
        available = max(0, max_tokens - self.token_counter.count_text(labels))
        previous_budget = int(available * 0.45)
        history_budget = available - previous_budget
        previous_part = self.token_counter.truncate(previous, previous_budget)
        history_part = self.token_counter.truncate(history, history_budget)
        combined = f"已有会话摘要：\n{previous_part}\n\n后续会话记录：\n{history_part}"
        return self.token_counter.truncate(combined, max_tokens)


class LlmConversationSummarizer:
    """使用 LLM 增量更新会话摘要，并在失败时保真回退。"""

    def __init__(
        self,
        llm_client: Any,
        token_counter: TokenCounter,
        *,
        fallback: DeterministicConversationSummarizer | None = None,
    ) -> None:
        self.llm_client = llm_client
        self.token_counter = token_counter
        self.fallback = fallback or DeterministicConversationSummarizer(token_counter)

    async def summarize(
        self,
        *,
        previous_summary: str,
        messages: Sequence[MemoryRecord],
        max_tokens: int,
    ) -> str:
        """合并旧摘要和新增历史，禁止补充对话中不存在的信息。"""
        prompt = (
            "你是会话历史摘要器。请将已有摘要与新增历史合并成一份可供"
            "后续对话继续使用的高保真摘要。\n"
            "必须保留：用户明确目标、约束、指代对象、关键结论、未完成事项、"
            "已确认修正、重要名称和数值。省略寒暄、重复表达和隐藏思考。"
            "不得创造历史中不存在的事实。只输出摘要正文。\n"
            f"已有摘要：\n{previous_summary or '无'}\n"
            f"新增历史：\n{_history_text(messages)}"
        )
        try:
            result = await self._invoke(prompt)
            if not result.strip():
                raise ValueError("LLM 返回了空摘要")
            return self.token_counter.truncate(
                result.strip(),
                max_tokens,
                marker="\n[较早会话摘要已达到预算上限]",
            )
        except Exception:
            return await self.fallback.summarize(
                previous_summary=previous_summary,
                messages=messages,
                max_tokens=max_tokens,
            )

    async def _invoke(self, prompt: str) -> str:
        ainvoke_auto = getattr(self.llm_client, "ainvoke_auto", None)
        if ainvoke_auto is not None:
            response = await ainvoke_auto(prompt)
            return str(getattr(response, "content", response) or "")
        response = await self.llm_client.ainvoke(prompt)
        if isinstance(response, str):
            return response
        return str(getattr(response, "content", response) or "")


__all__ = [
    "DeterministicConversationSummarizer",
    "LlmConversationSummarizer",
]
