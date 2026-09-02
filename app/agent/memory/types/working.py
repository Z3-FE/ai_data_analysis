"""LangGraph Working Memory 的读取适配器。

Working Memory 不复制 conversation_messages，也不写 agent_memories。正式状态由
AgentState.messages 保存，并由 AsyncPostgresSaver 按 thread_id 持久化。这个
适配器只读取已有状态，供后续 ContextEngine 统一调用。
"""

import re
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from app.agent.memory.enums import MemoryScope, MemoryStatus, MemoryType
from app.agent.memory.interfaces import MemoryCreate, MemoryRecord, MemorySearchResult

# 根据 conversation_id 读取 LangGraph StateSnapshot.values 或等价字典。
WorkingStateLoader = Callable[[str], Awaitable[Any]]


def create_working_state_loader(graph: Any) -> WorkingStateLoader:
    """把 CompiledStateGraph.aget_state 适配为按会话读取状态的函数。"""

    async def load(conversation_id: str) -> Any:
        return await graph.aget_state({"configurable": {"thread_id": conversation_id}})

    return load


def _terms(text: str) -> set[str]:
    """生成适合中英文短消息的轻量词项。"""
    return set(re.findall(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]", text.lower()))


def _message_text(message: Any) -> str:
    """从 LangChain 消息或兼容字典中读取纯文本。"""
    content = (
        message.get("content", "")
        if isinstance(message, Mapping)
        else getattr(message, "content", "")
    )
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return str(content) if content is not None else ""


def _message_role(message: Any) -> str:
    """读取消息角色，保留在临时记录的结构化字段中。"""
    if isinstance(message, Mapping):
        return str(message.get("role") or message.get("type") or "unknown")
    return str(getattr(message, "type", message.__class__.__name__.lower()))


class WorkingMemory:
    """从 Checkpointer-backed AgentState 读取当前线程短期记忆。"""

    def __init__(
        self, state_loader: WorkingStateLoader, *, max_items: int = 50
    ) -> None:
        # 状态加载器由集成层提供，本模块不依赖具体 CompiledStateGraph 实例。
        self.state_loader = state_loader
        # 单次最多返回的消息数量，避免无界读取。
        self.max_items = max(1, max_items)

    async def add(self, request: MemoryCreate) -> MemoryRecord:
        """拒绝旁路写入，Working Memory 只能随 Agent 图状态更新。"""
        del request
        raise RuntimeError(
            "Working Memory 由 AgentState.messages + Checkpointer 写入，"
            "不能通过 MemoryManager.add 单独写入"
        )

    async def load(
        self,
        *,
        user_id: str,
        conversation_id: str,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        """按时间顺序读取当前线程最近的消息。"""
        snapshot = await self.state_loader(conversation_id)
        # dict 本身也有 values 方法，必须先判断 Mapping，不能直接 getattr。
        values = (
            snapshot
            if isinstance(snapshot, Mapping)
            else getattr(snapshot, "values", None)
        )
        if not isinstance(values, Mapping):
            raise TypeError("Working Memory 状态加载器必须返回 StateSnapshot 或字典")
        state_user_id = str(values.get("user_id") or "")
        # Checkpointer 只按 thread_id 定位状态；用户身份缺失或不匹配时均拒绝返回。
        if not state_user_id or state_user_id != user_id:
            return []

        raw_messages = values.get("messages", [])
        messages = list(raw_messages) if isinstance(raw_messages, (list, tuple)) else []
        requested_limit = self.max_items if limit is None else max(0, limit)
        selected_count = min(requested_limit, self.max_items)
        if selected_count == 0:
            return []
        selected = messages[-selected_count:]
        now = datetime.now(timezone.utc)
        records: list[MemoryRecord] = []
        start_index = len(messages) - len(selected)
        for offset, message in enumerate(selected):
            content = _message_text(message).strip()
            if not content:
                continue
            message_id = (
                message.get("id")
                if isinstance(message, Mapping)
                else getattr(message, "id", None)
            )
            records.append(
                MemoryRecord(
                    memory_id=str(
                        message_id
                        or f"working:{conversation_id}:{start_index + offset}"
                    ),
                    user_id=user_id,
                    memory_type=MemoryType.WORKING,
                    scope=MemoryScope.CONVERSATION,
                    conversation_id=conversation_id,
                    project_id=None,
                    content=content,
                    structured_data={
                        "role": _message_role(message),
                        "message_index": start_index + offset,
                    },
                    status=MemoryStatus.ACTIVE,
                    version=1,
                    importance=0.5,
                    confidence=1.0,
                    supersedes_memory_id=None,
                    expires_at=None,
                    created_at=now,
                    updated_at=now,
                )
            )
        return records

    async def search(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        query: str,
        limit: int,
        project_id: str | None = None,
        modality: str | None = None,
    ) -> list[MemorySearchResult]:
        """在当前线程消息中进行轻量词项匹配。"""
        del project_id, modality
        if not conversation_id or limit <= 0:
            return []
        records = await self.load(
            user_id=user_id,
            conversation_id=conversation_id,
            limit=max(limit * 3, limit),
        )
        query_terms = _terms(query)
        candidates: list[MemorySearchResult] = []
        total = max(1, len(records))
        for index, record in enumerate(records):
            content_terms = _terms(record.content)
            overlap = len(query_terms & content_terms) / max(1, len(query_terms))
            substring = (
                1.0 if query and query.lower() in record.content.lower() else 0.0
            )
            similarity = max(overlap, substring)
            if similarity > 0 or not query.strip():
                # 同等相关性时稍微偏向较新的消息，但不改变召回语义。
                recency = (index + 1) / total
                candidates.append(
                    MemorySearchResult(
                        memory=record,
                        similarity=similarity,
                        score=(similarity if query.strip() else 1.0)
                        * (0.9 + recency * 0.1),
                        source="working",
                    )
                )
        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates[:limit]

    async def forget(self, memory_id: str, user_id: str) -> bool:
        """拒绝旁路删除，线程消息应由会话或 Checkpointer 管理接口处理。"""
        del memory_id, user_id
        raise RuntimeError("Working Memory 不能通过长期记忆接口单独删除")


__all__ = [
    "WorkingMemory",
    "WorkingStateLoader",
    "create_working_state_loader",
]
