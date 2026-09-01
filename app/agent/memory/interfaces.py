"""记忆基础设施接口。

Data Agent 依赖这些契约，不直接依赖 PostgreSQL、Qdrant 或对象存储客户端。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from .models import MemoryItem, MemoryMatch, MemoryReadRequest, MemoryScope


@runtime_checkable
class MemoryProvider(Protocol):
    """记忆事实的异步存取契约。"""

    async def add(self, item: MemoryItem) -> MemoryItem:
        """新增一条记忆，并返回实际保存的记录。"""
        ...

    async def search(self, request: MemoryReadRequest) -> list[MemoryMatch]:
        """在请求作用域内精确读取或检索记忆。"""
        ...

    async def update(self, item: MemoryItem) -> MemoryItem:
        """更新一条已有记忆，并保留存储层的版本管理能力。"""
        ...

    async def archive(
        self,
        memory_id: str,
        *,
        scope: MemoryScope,
        reason: str | None = None,
    ) -> MemoryItem | None:
        """归档作用域内的记忆；不存在时返回 ``None``。"""
        ...


__all__ = ["MemoryProvider"]
