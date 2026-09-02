"""持久化记忆类型的共同实现。"""

from datetime import datetime, timezone
from typing import Any

from app.agent.memory.enums import MemoryScope, MemoryStatus, MemoryType
from app.agent.memory.interfaces import (
    MemoryCreate,
    MemoryRecord,
    MemorySearchResult,
    MemoryRepository,
    VectorMemoryRepository,
)
from app.agent.memory.ranker import MemoryRanker


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
    return True


class PersistentMemory:
    """PostgreSQL 事实记录加可选向量索引的通用记忆类型。"""

    def __init__(
        self,
        *,
        memory_type: MemoryType,
        repository: MemoryRepository,
        vector_repository: VectorMemoryRepository | None = None,
        encoder: Any = None,
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
        # 统一的结果排序器。
        self.ranker = ranker or MemoryRanker()

    async def add(self, request: MemoryCreate) -> MemoryRecord:
        """先保存事实，再尽力同步向量索引。"""
        record, _ = await self.add_with_index_status(request)
        return record

    async def add_with_index_status(
        self, request: MemoryCreate
    ) -> tuple[MemoryRecord, bool]:
        """保存事实并返回向量是否同步成功，供附件等调用方更新状态。"""
        if request.memory_type is not self.memory_type:
            raise ValueError(f"该类型只接受 memory_type={self.memory_type.value}")
        record = await self.repository.create(request)
        indexed = await self._index(record)
        return record, indexed

    async def search(
        self,
        *,
        user_id: str,
        query: str,
        limit: int = 5,
        conversation_id: str | None = None,
        project_id: str | None = None,
        modality: str | None = None,
    ) -> list[MemorySearchResult]:
        """优先向量召回，向量不可用时回退 PostgreSQL 词法检索。"""
        if limit <= 0:
            return []
        candidates: list[tuple[MemoryRecord, float, str]] = []
        if self.vector_repository is not None and self.encoder is not None:
            try:
                vector = await self.encoder.encode(query)
                vector_items = await self.vector_repository.search(
                    user_id=user_id,
                    memory_type=self.memory_type,
                    vector=vector,
                    limit=max(limit * 3, limit),
                    conversation_id=conversation_id,
                    project_id=project_id,
                    modality=modality,
                )
                for item in vector_items:
                    record = await self.repository.get(item["memory_id"], user_id)
                    if record is not None and is_memory_visible(
                        record,
                        memory_type=self.memory_type,
                        conversation_id=conversation_id,
                        project_id=project_id,
                        modality=modality,
                    ):
                        candidates.append(
                            (record, float(item.get("score", 0.0)), "qdrant")
                        )
            except Exception:
                # 向量服务暂时不可用时，事实库检索仍然可以提供可用结果。
                candidates.clear()
        if not candidates:
            records = await self.repository.search(
                user_id=user_id,
                memory_type=self.memory_type,
                query=query,
                limit=max(limit * 3, limit),
                conversation_id=conversation_id,
                project_id=project_id,
                modality=modality,
            )
            candidates.extend((record, score, "postgres") for record, score in records)
        results = self.ranker.rank(candidates, limit)
        await self.repository.touch_access(
            [item.memory.memory_id for item in results],
            user_id,
        )
        return results

    async def replace(
        self, memory_id: str, user_id: str, request: MemoryCreate
    ) -> MemoryRecord:
        """原子创建新事实版本，并尽力替换旧向量投影。"""
        if request.memory_type is not self.memory_type:
            raise ValueError(f"该类型只接受 memory_type={self.memory_type.value}")
        old_record, new_record = await self.repository.replace(
            memory_id, user_id, request
        )
        if self.vector_repository is None:
            await self.repository.enqueue_index_job(memory_id, "qdrant")
        else:
            try:
                await self.vector_repository.delete(old_record)
            except Exception as exc:
                await self.repository.enqueue_index_job(memory_id, "qdrant", str(exc))
        await self._index(new_record)
        return new_record

    async def index_record(self, record: MemoryRecord) -> bool:
        """公开一次索引同步，供生命周期重试器调用。"""
        return await self._index(record)

    async def update(
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

    async def forget(self, memory_id: str, user_id: str) -> bool:
        """标记事实为 forgotten 并删除检索投影。"""
        record = await self.repository.get(memory_id, user_id)
        if record is None:
            return False
        changed = await self.repository.mark_status(
            memory_id, user_id, MemoryStatus.FORGOTTEN
        )
        if changed:
            if self.vector_repository is None:
                await self.repository.enqueue_index_job(memory_id, "qdrant")
            else:
                try:
                    await self.vector_repository.delete(record)
                except Exception as exc:
                    await self.repository.enqueue_index_job(
                        memory_id, "qdrant", str(exc)
                    )
        return changed

    async def _index(self, record: MemoryRecord) -> bool:
        """同步文本向量；索引故障不回滚已保存的事实。"""
        if self.vector_repository is None or self.encoder is None:
            # 当前基础设施未启用也要留下重建入口，维护器会等依赖可用后消费。
            await self.repository.enqueue_index_job(record.memory_id, "qdrant")
            return False
        try:
            vector = await self.encoder.encode(record.content)
            await self.vector_repository.upsert(record, vector)
            return True
        except Exception as exc:
            await self.repository.enqueue_index_job(
                record.memory_id, "qdrant", str(exc)
            )
            return False
