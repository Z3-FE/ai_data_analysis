"""四类记忆的业务调用门面。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .interfaces import MemoryProvider
from .models import (
    MemoryItem,
    MemoryMatch,
    MemoryReadRequest,
    MemoryScope,
    MemorySource,
    MemoryType,
)


class MemoryManager:
    """统一调用四类记忆，不让 Agent 节点直接操作数据库。"""

    def __init__(self, provider: MemoryProvider) -> None:
        # Provider 负责事实存储；Manager 负责类型化的业务调用入口。
        self.provider = provider

    async def save(self, item: MemoryItem) -> MemoryItem:
        """保存一条已经确定类型和来源的记忆。"""
        return await self.provider.add(item)

    async def read(
        self,
        *,
        scope: MemoryScope,
        query: str = "",
        memory_types: Iterable[MemoryType | str] | None = None,
        asset_ids: Iterable[str] = (),
        limit: int = 20,
        include_archived: bool = False,
        # Working Memory 使用时要求会话 ID 精确匹配，避免跨会话串用。
        exact_conversation: bool = False,
    ) -> list[MemoryMatch]:
        """按需读取记忆；Working Memory 可要求会话 ID 精确匹配。"""
        types = frozenset(MemoryType if memory_types is None else memory_types)
        request = MemoryReadRequest(
            scope=scope,
            query=query,
            memory_types=types,
            asset_ids=tuple(asset_ids),
            limit=limit,
            include_archived=include_archived,
            exact_conversation=exact_conversation,
        )
        return await self.provider.search(request)

    async def save_working(
        self, content: str, *, scope: MemoryScope, source: MemorySource, **kwargs: Any
    ) -> MemoryItem:
        """保存当前会话活动状态、约束、任务或资源引用。"""
        return await self._save_type(MemoryType.WORKING, content, scope, source, kwargs)

    async def save_episodic(
        self, content: str, *, scope: MemoryScope, source: MemorySource, **kwargs: Any
    ) -> MemoryItem:
        """保存已经完成的任务经历、结果摘要或失败修正经验。"""
        return await self._save_type(MemoryType.EPISODIC, content, scope, source, kwargs)

    async def save_semantic(
        self, content: str, *, scope: MemoryScope, source: MemorySource, **kwargs: Any
    ) -> MemoryItem:
        """保存稳定事实、业务约定、指标定义或用户明确偏好。"""
        return await self._save_type(MemoryType.SEMANTIC, content, scope, source, kwargs)

    async def save_perceptual(
        self, content: str, *, scope: MemoryScope, source: MemorySource, **kwargs: Any
    ) -> MemoryItem:
        """保存资源身份和 OCR、转写、版面等派生观察。"""
        return await self._save_type(MemoryType.PERCEPTUAL, content, scope, source, kwargs)

    async def read_working(
        self, *, scope: MemoryScope, limit: int = 20
    ) -> list[MemoryMatch]:
        """精确读取当前会话的 Working Memory。"""
        return await self.read(
            scope=scope,
            memory_types={MemoryType.WORKING},
            limit=limit,
            exact_conversation=True,
        )

    async def read_episodic(
        self, *, scope: MemoryScope, query: str, limit: int = 5
    ) -> list[MemoryMatch]:
        """按当前问题关键词读取 Episodic Memory。"""
        return await self.read(
            scope=scope,
            query=query,
            memory_types={MemoryType.EPISODIC},
            limit=limit,
        )

    async def read_semantic(
        self, *, scope: MemoryScope, query: str = "", limit: int = 10
    ) -> list[MemoryMatch]:
        """读取相关 Semantic Memory，后续可由向量 Provider 替换检索实现。"""
        return await self.read(
            scope=scope,
            query=query,
            memory_types={MemoryType.SEMANTIC},
            limit=limit,
        )

    async def read_perceptual(
        self, *, scope: MemoryScope, asset_ids: Iterable[str], limit: int = 10
    ) -> list[MemoryMatch]:
        """根据 asset_id 精确读取 Perceptual Memory。"""
        return await self.read(
            scope=scope,
            memory_types={MemoryType.PERCEPTUAL},
            asset_ids=asset_ids,
            limit=limit,
        )

    async def update(self, item: MemoryItem) -> MemoryItem:
        """更新一条已经存在的记忆。"""
        return await self.provider.update(item)

    async def archive(
        self, memory_id: str, *, scope: MemoryScope, reason: str | None = None
    ) -> MemoryItem | None:
        """归档记忆而不是物理删除，保留后续审计和恢复能力。"""
        return await self.provider.archive(memory_id, scope=scope, reason=reason)

    async def _save_type(
        self,
        memory_type: MemoryType,
        content: str,
        scope: MemoryScope,
        source: MemorySource,
        options: dict[str, Any],
    ) -> MemoryItem:
        """把类型化保存入口统一转换为 MemoryItem。"""
        item = MemoryItem(
            memory_type=memory_type,
            content=content,
            scope=scope,
            source=source,
            structured_data=dict(options.pop("structured_data", {})),
            metadata=dict(options.pop("metadata", {})),
            importance=options.pop("importance", 0.5),
            confidence=options.pop("confidence", 0.5),
        )
        if options:
            raise TypeError(f"不支持的记忆参数：{', '.join(sorted(options))}")
        return await self.save(item)


__all__ = ["MemoryManager"]
