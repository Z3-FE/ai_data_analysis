"""四类记忆的统一门面。"""

import asyncio

from typing import Any

from app.agent.memory.enums import MemoryType
from app.agent.memory.interfaces import (
    MemoryAsset,
    MemoryCreate,
    MemorySearchResult,
)
from app.agent.memory.types.episodic import EpisodicMemory
from app.agent.memory.types.perceptual import PerceptualMemory
from app.agent.memory.types.semantic import SemanticMemory
from app.agent.memory.types.working import WorkingMemory


class MemoryManager:
    """为上层 Agent 提供统一的记忆读写入口。"""

    def __init__(
        self,
        *,
        episodic: EpisodicMemory | None = None,
        semantic: SemanticMemory | None = None,
        perceptual: PerceptualMemory | None = None,
        working: WorkingMemory | None = None,
    ) -> None:
        # 四类记忆按类型注册，调用方可以按需启用或替换实现。
        self._memories: dict[MemoryType, Any] = {
            MemoryType.WORKING: working,
            MemoryType.EPISODIC: episodic,
            MemoryType.SEMANTIC: semantic,
            MemoryType.PERCEPTUAL: perceptual,
        }

    def get(self, memory_type: MemoryType) -> Any:
        """获取指定类型的实现；未启用时给出清晰错误。"""
        memory = self._memories.get(memory_type)
        if memory is None:
            raise RuntimeError(f"记忆类型 {memory_type.value} 尚未启用")
        return memory

    async def add(self, request: MemoryCreate):
        """根据 request.memory_type 写入对应记忆。"""
        return await self.get(request.memory_type).add(request)

    async def replace(
        self,
        memory_type: MemoryType,
        memory_id: str,
        user_id: str,
        request: MemoryCreate,
    ):
        """创建新版本并把旧版本标记为 superseded。"""
        if request.memory_type is not memory_type:
            raise ValueError("memory_type 与替换请求不一致")
        return await self.get(memory_type).replace(memory_id, user_id, request)

    async def search(
        self,
        *,
        memory_type: MemoryType,
        user_id: str,
        query: str,
        limit: int = 5,
        conversation_id: str | None = None,
        project_id: str | None = None,
        modality: str | None = None,
    ) -> list[MemorySearchResult]:
        """检索单一记忆类型，避免默认把四类记忆全部扫一遍。"""
        return await self.get(memory_type).search(
            user_id=user_id,
            query=query,
            limit=limit,
            conversation_id=conversation_id,
            project_id=project_id,
            modality=(modality if memory_type is MemoryType.PERCEPTUAL else None),
        )

    async def search_many(
        self,
        *,
        memory_types: list[MemoryType],
        user_id: str,
        query: str,
        limit: int = 10,
        conversation_id: str | None = None,
        project_id: str | None = None,
        modality: str | None = None,
    ) -> list[MemorySearchResult]:
        """显式组合多类记忆，并按综合得分合并去重。"""
        if limit <= 0:
            return []
        # 检索彼此独立，按类型并发执行；保持输入顺序只用于稳定合并。
        unique_types = list(dict.fromkeys(memory_types))
        groups = await asyncio.gather(
            *(
                self.search(
                    memory_type=memory_type,
                    user_id=user_id,
                    query=query,
                    limit=limit,
                    conversation_id=conversation_id,
                    project_id=project_id,
                    modality=(
                        modality if memory_type is MemoryType.PERCEPTUAL else None
                    ),
                )
                for memory_type in unique_types
            )
        )
        results = [result for group in groups for result in group]
        unique: dict[str, MemorySearchResult] = {}
        for result in results:
            old = unique.get(result.memory.memory_id)
            if old is None or result.score > old.score:
                unique[result.memory.memory_id] = result
        return sorted(unique.values(), key=lambda item: item.score, reverse=True)[
            :limit
        ]

    async def forget(
        self, memory_type: MemoryType, memory_id: str, user_id: str
    ) -> bool:
        """遗忘一条指定类型的记忆。"""
        return await self.get(memory_type).forget(memory_id, user_id)

    async def register_asset(self, asset: MemoryAsset) -> MemoryAsset:
        """登记感知附件；当前只有文本内容可转为记忆。"""
        perceptual = self.get(MemoryType.PERCEPTUAL)
        return await perceptual.register_asset(asset)
