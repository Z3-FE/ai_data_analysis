"""Working Memory 窗口和持久化增量摘要。"""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import uuid4

from app.agent.context_engine.contracts import (
    ContextConversationSummary,
    ContextItem,
    ContextPolicy,
    ContextSourceKind,
    ContextSourceRef,
)
from app.agent.context_engine.interfaces import (
    ContextRepository,
    ConversationSummarizer,
    TokenCounter,
)
from app.agent.memory.interfaces import MemoryRecord
from app.agent.memory.lexical import lexical_scores


@dataclass(frozen=True, slots=True)
class PreparedHistory:
    """历史窗口处理后交给 Gather 阶段的结果。"""

    items: tuple[ContextItem, ...]
    summary: ContextConversationSummary | None
    summary_updated: bool


class ConversationHistoryManager:
    """保留近期原始消息，并增量摘要被窗口挤出的较早消息。"""

    def __init__(
        self,
        *,
        context_repository: ContextRepository,
        summarizer: ConversationSummarizer,
        token_counter: TokenCounter,
        policy: ContextPolicy,
    ) -> None:
        self.context_repository = context_repository
        self.summarizer = summarizer
        self.token_counter = token_counter
        self.policy = policy

    async def prepare(
        self,
        *,
        user_id: str,
        conversation_id: str,
        query: str,
        working: list[MemoryRecord],
        token_budget: int,
    ) -> PreparedHistory:
        """按消息索引更新摘要，再返回摘要和未覆盖的近期消息。"""
        ordered = sorted(working, key=self._message_index)
        existing = await self.context_repository.get_summary(user_id, conversation_id)
        covered_through = existing.covered_through_index if existing is not None else -1
        uncovered = [
            record
            for record in ordered
            if self._message_index(record) > covered_through
        ]
        recent_budget = max(
            self.policy.min_compression_tokens,
            int(max(0, token_budget) * self.policy.recent_history_ratio),
        )
        recent, older = self._split_recent(uncovered, recent_budget)
        summary = existing
        summary_updated = False
        if older:
            summary = await self._update_summary(
                existing=existing,
                user_id=user_id,
                conversation_id=conversation_id,
                older=older,
            )
            summary_updated = True

        items: list[ContextItem] = []
        if summary is not None and summary.content.strip():
            items.append(
                ContextItem(
                    item_id=summary.summary_id,
                    source_kind=ContextSourceKind.SUMMARY,
                    content=summary.content,
                    token_count=self.token_counter.count_text(summary.content),
                    relevance=0.8,
                    importance=0.85,
                    confidence=0.9,
                    recency=0.45,
                    priority=75,
                    structured_data={
                        "covered_from_index": summary.covered_from_index,
                        "covered_through_index": summary.covered_through_index,
                        "version": summary.version,
                    },
                    source_refs=(
                        ContextSourceRef(
                            source_type="conversation_summary",
                            source_id=summary.summary_id,
                        ),
                    ),
                )
            )
        items.extend(self._working_items(recent, query))
        return PreparedHistory(tuple(items), summary, summary_updated)

    def _split_recent(
        self, records: list[MemoryRecord], token_budget: int
    ) -> tuple[list[MemoryRecord], list[MemoryRecord]]:
        """从后向前选择可放入窗口的完整消息，至少保留最近一条。"""
        if not records:
            return [], []
        selected: list[MemoryRecord] = []
        used = 0
        for record in reversed(records):
            tokens = self.token_counter.count_text(record.content) + 6
            if selected and used + tokens > token_budget:
                break
            selected.append(record)
            used += tokens
        selected.reverse()
        split_at = len(records) - len(selected)
        return selected, records[:split_at]

    async def _update_summary(
        self,
        *,
        existing: ContextConversationSummary | None,
        user_id: str,
        conversation_id: str,
        older: list[MemoryRecord],
    ) -> ContextConversationSummary:
        # 长历史分批摘要，避免摘要器自己的输入先突破模型上下文上限。
        content = existing.content if existing else ""
        for batch in self._summary_batches(older):
            content = await self.summarizer.summarize(
                previous_summary=content,
                messages=batch,
                max_tokens=self.policy.summary_max_tokens,
            )
        now = datetime.now(UTC)
        first_index = self._message_index(older[0])
        last_index = self._message_index(older[-1])
        summary = ContextConversationSummary(
            summary_id=existing.summary_id if existing else str(uuid4()),
            user_id=user_id,
            conversation_id=conversation_id,
            content=content,
            covered_from_index=(
                existing.covered_from_index if existing else first_index
            ),
            covered_through_index=last_index,
            source_message_count=(
                (existing.source_message_count if existing else 0) + len(older)
            ),
            token_count=self.token_counter.count_text(content),
            version=(existing.version + 1 if existing else 1),
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        return await self.context_repository.save_summary(summary)

    def _summary_batches(self, records: list[MemoryRecord]) -> list[list[MemoryRecord]]:
        """按 token 预算切分新增历史；单条超长消息由摘要器输出预算处理。"""
        batches: list[list[MemoryRecord]] = []
        current: list[MemoryRecord] = []
        used = 0
        content_budget = max(1, self.policy.summary_batch_tokens - 6)
        for record in records:
            chunks = self.token_counter.split_text(record.content, content_budget) or [
                ""
            ]
            for chunk_index, chunk in enumerate(chunks):
                part = replace(
                    record,
                    memory_id=f"{record.memory_id}:summary-part:{chunk_index}",
                    content=chunk,
                )
                tokens = self.token_counter.count_text(chunk) + 6
                if current and used + tokens > self.policy.summary_batch_tokens:
                    batches.append(current)
                    current = []
                    used = 0
                current.append(part)
                used += tokens
        if current:
            batches.append(current)
        return batches

    def _working_items(
        self, records: list[MemoryRecord], query: str
    ) -> list[ContextItem]:
        similarities = lexical_scores(query, [record.content for record in records])
        total = max(1, len(records))
        items = []
        for position, (record, similarity) in enumerate(
            zip(records, similarities, strict=True), start=1
        ):
            recency = 0.55 + 0.45 * (position / total)
            items.append(
                ContextItem(
                    item_id=record.memory_id,
                    source_kind=ContextSourceKind.WORKING,
                    content=record.content,
                    role=str(record.structured_data.get("role") or "user"),
                    token_count=self.token_counter.count_text(record.content),
                    # 指代问题的词法相似度可能很低，近期消息仍保留基础相关度。
                    relevance=max(0.35, similarity),
                    importance=record.importance,
                    confidence=1.0,
                    recency=recency,
                    priority=min(90, 55 + position),
                    structured_data=dict(record.structured_data),
                    source_refs=(
                        ContextSourceRef(
                            source_type="message",
                            source_id=record.memory_id,
                        ),
                    ),
                )
            )
        return items

    @staticmethod
    def _message_index(record: MemoryRecord) -> int:
        raw = record.structured_data.get("message_index", -1)
        try:
            return int(raw)
        except (TypeError, ValueError):
            return -1


__all__ = ["ConversationHistoryManager", "PreparedHistory"]
