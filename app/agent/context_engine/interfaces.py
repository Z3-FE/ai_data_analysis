"""ContextEngine 的可替换依赖端口。"""

from collections.abc import Sequence
from typing import Protocol

from app.agent.context_engine.contracts import (
    ContextItem,
    ContextKnowledgeItem,
    ContextRequest,
    ContextRetrievalPlan,
    ReferenceResolution,
)
from app.agent.memory.interfaces import MemoryContextReader, MemoryRecord


class TokenCounter(Protocol):
    """模型 tokenizer 的最小能力。"""

    def count_text(self, text: str) -> int: ...

    def count_messages(self, messages: Sequence[dict]) -> int: ...

    def truncate(self, text: str, max_tokens: int, *, marker: str = "") -> str: ...

    def split_text(self, text: str, max_tokens: int) -> list[str]: ...


class ContextPlanner(Protocol):
    """根据当前问题决定需要召回哪些来源。"""

    async def plan(
        self,
        request: ContextRequest,
        working: Sequence[MemoryRecord],
        resolution: ReferenceResolution,
    ) -> ContextRetrievalPlan: ...


class ConversationSummarizer(Protocol):
    """把较早会话和已有摘要压缩成新摘要。"""

    async def summarize(
        self,
        *,
        previous_summary: str,
        messages: Sequence[MemoryRecord],
        max_tokens: int,
    ) -> str: ...


class ContextCompressor(Protocol):
    """在单次构建内压缩超出预算的候选，不产生持久化摘要。"""

    async def compress(self, item: ContextItem, *, max_tokens: int) -> str: ...


class ContextKnowledgeRetriever(Protocol):
    """外部知识库或业务 RAG 的可选召回端口。"""

    async def search(
        self,
        *,
        user_id: str,
        query: str,
        limit: int,
        project_id: str | None = None,
    ) -> list[ContextKnowledgeItem]: ...


__all__ = [
    "ContextKnowledgeRetriever",
    "ContextCompressor",
    "ContextPlanner",
    "ConversationSummarizer",
    "MemoryContextReader",
    "TokenCounter",
]
