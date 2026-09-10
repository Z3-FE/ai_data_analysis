"""持久化记忆类型的共同实现。"""

import asyncio
from datetime import datetime, timezone

from app.agent.memory.encoders.base import MemoryEncoder
from app.agent.memory.enums import MemoryScope, MemoryStatus, MemoryType
from app.agent.memory.interfaces import (
    MemoryRecord,
    MemoryRepository,
    MemorySearchResult,
    MemoryWriteResult,
    VectorMemoryRepository,
)
from app.agent.memory.ranker import MemoryRanker, MemoryRecallCandidate


def is_memory_retrievable(record: MemoryRecord) -> bool:
    """判断事实是否仍可召回，不依赖索引投影中的滞后状态。"""
    if record.status is not MemoryStatus.ACTIVE:
        return False
    if record.expires_at is None:
        return True
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > datetime.now(timezone.utc)


def is_memory_visible(
    record: MemoryRecord,
    *,
    memory_type: MemoryType,
    conversation_id: str | None,
    project_id: str | None,
    modality: str | None = None,
    asset_ids: list[str] | None = None,
) -> bool:
    """使用 PostgreSQL 事实复核投影候选的类型、作用域和模态。"""
    if record.memory_type is not memory_type or not is_memory_retrievable(record):
        return False
    if record.scope is MemoryScope.CONVERSATION and (
        conversation_id is None or record.conversation_id != conversation_id
    ):
        return False
    if record.scope is MemoryScope.PROJECT and (
        project_id is None or record.project_id != project_id
    ):
        return False
    if modality is not None and record.structured_data.get("modality") != modality:
        return False
    if asset_ids is not None and (
        str(record.structured_data.get("asset_id") or "") not in asset_ids
    ):
        return False
    return True


class PersistentMemory:
    """PostgreSQL 事实记录加可选向量索引的通用记忆类型。"""

    def __init__(
        self,
        *,
        memory_type: MemoryType,
        repository: MemoryRepository,
        vector_repository: VectorMemoryRepository | None = None,
        encoder: MemoryEncoder | None = None,
        ranker: MemoryRanker | None = None,
    ) -> None:
        # 该实例只允许操作一种长期记忆类型。
        self.memory_type = memory_type
        # PostgreSQL 事实仓储。
        self.repository = repository
        # 可选 Qdrant 投影仓储；没有时仍然可以使用 PostgreSQL 词法回退。
        self.vector_repository = vector_repository
        # 文本向量编码器；当前由项目 Embedding 服务提供。
        self.encoder = encoder
        # 每种记忆拥有自己的评分策略，ContextEngine 只消费统一结果。
        self.ranker = ranker or MemoryRanker(memory_type)

    async def search(
        self,
        *,
        user_id: str,
        query: str,
        limit: int = 5,
        conversation_id: str | None = None,
        project_id: str | None = None,
        modality: str | None = None,
        asset_ids: list[str] | None = None,
    ) -> list[MemorySearchResult]:
        """并行执行向量和词法召回，再按记忆类型融合全部信号。"""
        if limit <= 0:
            return []
        candidates = await self._recall_candidates(
            user_id=user_id,
            query=query,
            limit=limit,
            conversation_id=conversation_id,
            project_id=project_id,
            modality=modality,
            asset_ids=asset_ids,
        )
        results = self.ranker.rank(list(candidates.values()), limit)
        await self.repository.touch_access(
            [item.memory.memory_id for item in results],
            user_id,
        )
        return results

    async def _recall_candidates(
        self,
        *,
        user_id: str,
        query: str,
        limit: int,
        conversation_id: str | None,
        project_id: str | None,
        modality: str | None,
        asset_ids: list[str] | None,
    ) -> dict[str, MemoryRecallCandidate]:
        """收集并融合词法、向量和精确附件引用候选。"""
        recall_limit = max(limit * 4, limit)

        async def lexical_search():
            return await self.repository.search(
                user_id=user_id,
                memory_type=self.memory_type,
                query=query,
                limit=recall_limit,
                conversation_id=conversation_id,
                project_id=project_id,
                modality=modality,
                asset_ids=asset_ids,
            )

        async def vector_search():
            if self.vector_repository is None or self.encoder is None:
                return []
            try:
                vector = await self.encoder.encode(query)
                return await self.vector_repository.search(
                    user_id=user_id,
                    memory_type=self.memory_type,
                    vector=vector,
                    limit=recall_limit,
                    conversation_id=conversation_id,
                    project_id=project_id,
                    modality=modality,
                    asset_ids=asset_ids,
                )
            except Exception:
                # 投影暂时不可用不应阻断 PostgreSQL 事实召回。
                return []

        lexical_items, vector_items = await asyncio.gather(
            lexical_search(),
            vector_search(),
        )
        candidates: dict[str, MemoryRecallCandidate] = {}
        for record, score in lexical_items:
            candidate = candidates.setdefault(
                record.memory_id, MemoryRecallCandidate(memory=record)
            )
            candidate.add_signal("lexical", score)
        for item in vector_items:
            record = await self.repository.get(item["memory_id"], user_id)
            if record is None or not is_memory_visible(
                record,
                memory_type=self.memory_type,
                conversation_id=conversation_id,
                project_id=project_id,
                modality=modality,
                asset_ids=asset_ids,
            ):
                continue
            candidate = candidates.setdefault(
                record.memory_id, MemoryRecallCandidate(memory=record)
            )
            candidate.add_signal("vector", float(item.get("score", 0.0)))
        if asset_ids:
            for candidate in candidates.values():
                candidate.add_signal("reference", 1.0)
        return candidates

    async def _sync_write_result(self, result: MemoryWriteResult) -> bool:
        """同步 PostgreSQL 原子写入后的向量投影，并返回本次是否成功。"""
        if result.replaced_record is not None and self.vector_repository is not None:
            try:
                await self.vector_repository.delete(result.replaced_record)
            except Exception as exc:
                await self.repository.record_index_failure(
                    result.replaced_record.memory_id,
                    "qdrant",
                    str(exc),
                )
        return await self._index(result.record)

    async def _update(
        self,
        memory_id: str,
        user_id: str,
        *,
        importance: float | None = None,
        confidence: float | None = None,
        expires_at: datetime | None = None,
    ) -> MemoryRecord | None:
        """原地更新治理字段；正文或结构变化必须通过 replace 创建新版本。"""
        return await self.repository.update(
            memory_id,
            user_id,
            importance=importance,
            confidence=confidence,
            expires_at=expires_at,
        )

    async def _forget(self, memory_id: str, user_id: str) -> bool:
        """标记事实为 forgotten 并删除检索投影。"""
        record = await self.repository.get(memory_id, user_id)
        if record is None:
            return False
        changed = await self.repository.mark_status(
            memory_id, user_id, MemoryStatus.FORGOTTEN
        )
        if changed and self.vector_repository is not None:
            try:
                await self.vector_repository.delete(record)
            except Exception as exc:
                await self.repository.record_index_failure(
                    memory_id, "qdrant", str(exc)
                )
        return changed

    async def _index(self, record: MemoryRecord) -> bool:
        """同步文本向量；索引故障不回滚已保存的事实。"""
        if self.vector_repository is None or self.encoder is None:
            # 未提供投影依赖不是失败；只有已启用依赖的同步异常才记录失败。
            return False
        try:
            vector = await self.encoder.encode(record.content)
            await self.vector_repository.upsert(record, vector)
            return True
        except Exception as exc:
            await self.repository.record_index_failure(
                record.memory_id, "qdrant", str(exc)
            )
            return False
