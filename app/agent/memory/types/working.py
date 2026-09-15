"""Harness 使用的 PostgreSQL Working Memory。

Working Memory 是当前会话的可见消息历史，直接读取 Agent PostgreSQL 的
``conversation_messages``。Harness 自己负责运行现场和暂停恢复，不再通过旧
LangGraph Graph 读取另一份历史状态。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.memory.enums import MemoryScope, MemoryStatus, MemoryType
from app.agent.memory.interfaces import MemoryRecord, MemorySearchResult
from app.agent.memory.lexical import lexical_scores
from app.models.agent_history import ConversationMessageModel, ConversationModel


class WorkingMemory:
    """从 conversation_messages 读取当前会话的短期记忆。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        max_items: int | None = None,
    ) -> None:
        # Harness 和 Memory 共用 Agent PostgreSQL 连接池。
        self.session_factory = session_factory
        # ContextEngine 默认读取完整会话，再按 token 预算做窗口选择。
        self.max_items = max(1, max_items) if max_items is not None else None

    async def load(
        self,
        *,
        user_id: str,
        conversation_id: str,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        """按时间顺序读取当前线程最近的消息。"""
        async with self.session_factory() as session:
            messages = list(
                (
                    await session.scalars(
                        select(ConversationMessageModel)
                        .join(
                            ConversationModel,
                            ConversationModel.conversation_id
                            == ConversationMessageModel.conversation_id,
                        )
                        .where(
                            ConversationMessageModel.conversation_id == conversation_id,
                            ConversationModel.user_id == user_id,
                        )
                        .order_by(
                            ConversationMessageModel.created_at,
                            ConversationMessageModel.sequence_no,
                        )
                    )
                ).all()
            )

        requested_limit = len(messages) if limit is None else max(0, limit)
        selected_count = (
            min(requested_limit, self.max_items)
            if self.max_items is not None
            else requested_limit
        )
        if selected_count == 0:
            return []
        selected = messages[-selected_count:]
        records: list[MemoryRecord] = []
        start_index = len(messages) - len(selected)
        for offset, message in enumerate(selected):
            content = (message.content or "").strip()
            if not content:
                continue
            metadata = dict(message.message_metadata or {})
            asset_ids = metadata.get("asset_ids", [])
            if not isinstance(asset_ids, (list, tuple)):
                asset_ids = []
            raw_importance = metadata.get("memory_importance", 0.5)
            try:
                importance = max(0.0, min(1.0, float(raw_importance)))
            except (TypeError, ValueError):
                importance = 0.5
            records.append(
                MemoryRecord(
                    memory_id=f"working:{conversation_id}:{message.message_id}",
                    user_id=user_id,
                    memory_type=MemoryType.WORKING,
                    scope=MemoryScope.CONVERSATION,
                    conversation_id=conversation_id,
                    project_id=None,
                    content=content,
                    structured_data={
                        "role": message.role,
                        "message_index": start_index + offset,
                        "turn_id": message.turn_id,
                        "execution_mode": str(metadata.get("execution_mode") or ""),
                        "output_type": str(metadata.get("output_type") or ""),
                        "asset_ids": [
                            str(asset_id)
                            for asset_id in asset_ids
                            if str(asset_id).strip()
                        ],
                    },
                    status=MemoryStatus.ACTIVE,
                    version=1,
                    importance=importance,
                    confidence=1.0,
                    supersedes_memory_id=None,
                    expires_at=None,
                    created_at=message.created_at,
                    updated_at=message.created_at,
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
        similarities = lexical_scores(query, [record.content for record in records])
        candidates: list[MemorySearchResult] = []
        total = max(1, len(records))
        for index, (record, similarity) in enumerate(
            zip(records, similarities, strict=True)
        ):
            if similarity > 0 or not query.strip():
                # 对齐教程思想：词法相关性乘近因性，再乘重要性权重。
                recency = 0.85 + ((index + 1) / total) * 0.15
                importance_weight = 0.8 + record.importance * 0.4
                candidates.append(
                    MemorySearchResult(
                        memory=record,
                        similarity=similarity,
                        score=(similarity if query.strip() else 1.0)
                        * recency
                        * importance_weight,
                        source="working",
                        signals={
                            "lexical": similarity,
                            "recency": recency,
                            "importance": record.importance,
                        },
                    )
                )
        candidates.sort(key=lambda item: item.score, reverse=True)
        return candidates[:limit]


__all__ = [
    "WorkingMemory",
]
