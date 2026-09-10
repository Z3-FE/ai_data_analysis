"""长期记忆过期处理。"""

from typing import Any

from app.agent.memory.enums import MemoryStatus, MemoryType
from app.agent.memory.types.semantic import SemanticMemory


class MemoryLifecycle:
    """执行长期记忆的过期清理。"""

    def __init__(
        self,
        *,
        repository: Any,
        memories: dict[MemoryType, Any],
    ) -> None:
        # 生命周期任务使用同一个 PostgreSQL 事实仓储。
        self.repository = repository
        # 已启用的持久化记忆类型，用于按类型删除过期投影。
        self.memories = memories

    async def expire(self, limit: int = 100) -> int:
        """把到期的 active 记忆标记为 expired，并删除向量投影。"""
        expired = await self.repository.list_expired(limit)
        changed = 0
        for record in expired:
            memory = self.memories.get(record.memory_type)
            if memory is None:
                continue
            if await self.repository.mark_status(
                record.memory_id, record.user_id, MemoryStatus.EXPIRED
            ):
                if memory.vector_repository is not None:
                    try:
                        await memory.vector_repository.delete(record)
                    except Exception as exc:
                        await self.repository.record_index_failure(
                            record.memory_id, "qdrant", str(exc)
                        )
                if isinstance(memory, SemanticMemory):
                    if memory.graph_repository is not None:
                        try:
                            await memory.graph_repository.delete_projection(
                                record.memory_id
                            )
                        except Exception as exc:
                            await self.repository.record_index_failure(
                                record.memory_id, "neo4j", str(exc)
                            )
                changed += 1
        return changed
