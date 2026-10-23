"""Harness 使用的 PostgreSQL Working Memory。

Working Memory 是当前会话的可见消息历史，直接读取 Agent PostgreSQL 的
``conversation_messages``。Harness 自己负责运行现场和暂停恢复，不再通过旧
LangGraph Graph 读取另一份历史状态。
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.memory.enums import MemoryScope, MemoryStatus, MemoryType
from app.agent.memory.interfaces import MemoryRecord, MemorySearchResult
from app.agent.memory.lexical import lexical_scores
from app.models.agent_history import ConversationMessageModel, ConversationModel, ConversationTurnModel
from app.agent.state_result_store.contracts import HarnessStatusType

logger = logging.getLogger(__name__)
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

        self.max_items = max(1, max_items) if max_items is not None else None # 系统最大返回条数

    async def load(
        self,
        *,
        user_id: str,
        conversation_id: str,
        limit: int | None = None, # 每次用户选择的返回条数，不能大于max_items
    ) -> list[MemoryRecord]:
        """按时间顺序读取当前线程最近的消息。本类只读不写：conversation_messages 的写入源头是
        conversation_repository.start_turn（user, seq=0）与 finish_turn（assistant, seq=1）。"""
        # ① 查库：按 created_at + sequence_no(轮次内顺序) 时间序取全量。
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
                            ConversationMessageModel.sequence_no, # 只有当 created_at 相同时，才按 sequence_no 排
                        )
                    )
                ).all()
            )
            excluded_turns = set((await session.scalars(
                select(ConversationTurnModel.turn_id).where(
                    ConversationTurnModel.conversation_id == conversation_id,
                    ConversationTurnModel.user_id == user_id,
                    ConversationTurnModel.status == HarnessStatusType.BUDGET_EXCEEDED,
                )
            )).all())
        logger.info("读取PG数据：读取用户与ai信息，表名称：conversation_messages")

        # 先保留全会话索引再过滤，避免摘要 covered_through_index 因排除轮次而漂移。
        indexed_messages = [
            (index, message) for index, message in enumerate(messages)
            if message.turn_id not in excluded_turns
        ]
        # ②
        requested_limit = len(indexed_messages) if limit is None else max(0, limit) # 返回的信息多少条
        selected_count = (
            min(requested_limit, self.max_items)
            if self.max_items is not None
            else requested_limit
        )
        if selected_count == 0:
            return []
        selected = indexed_messages[-selected_count:] # 取回来的最大条数
        # ③ 投影成 MemoryRecord
        records: list[MemoryRecord] = []
        for message_index, message in selected:
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
            # 伪 ID：working 记忆不落记忆库，由 conversation_id + message_id 合成，供 source_refs 追踪引用。
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
                        "message_index": message_index,
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
