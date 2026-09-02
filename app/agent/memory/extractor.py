"""为后续上下文工程准备的记忆提取端口。

本阶段不自动从 Agent 对话中提炼记忆，避免在尚未定义上下文策略时改变现有
Agent 行为。接入阶段只需要让上层提供明确的 MemoryCreate 列表即可。
"""

from collections.abc import Iterable

from app.agent.memory.interfaces import MemoryCreate


class MemoryExtractor:
    """接收上层已确认的记忆候选，不擅自把每条消息写成长期记忆。"""

    def extract(self, candidates: Iterable[MemoryCreate]) -> list[MemoryCreate]:
        """过滤空内容并返回候选；LLM 提取策略在上下文工程阶段接入。"""
        return [candidate for candidate in candidates if candidate.content.strip()]
