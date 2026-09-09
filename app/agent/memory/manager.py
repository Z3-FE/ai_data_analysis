"""四类记忆的统一门面。"""

import asyncio
import logging
from datetime import datetime
from typing import Any

from app.agent.memory.contracts import GovernedCandidate
from app.agent.memory.enums import MemoryDecisionAction, MemoryType
from app.agent.memory.interfaces import (
    MemoryAsset,
    MemoryRepository,
    MemoryRecord,
    MemorySearchResult,
    MemorySource,
    MemoryWriteResult,
)
from app.agent.memory.types.episodic import EpisodicMemory
from app.agent.memory.types.perceptual import PerceptualMemory
from app.agent.memory.types.semantic import SemanticMemory
from app.agent.memory.types.working import WorkingMemory

logger = logging.getLogger(__name__)


class MemoryManager:
    """为上层 Agent 和 ContextEngine 提供统一的记忆读写入口。

    上层只依赖这个门面：长期记忆的形成、治理、投影同步和版本处理都在门面
    内部完成，ContextEngine 只使用本类的只读方法。
    """

    def __init__(
        self,
        *,
        episodic: EpisodicMemory | None = None,
        semantic: SemanticMemory | None = None,
        perceptual: PerceptualMemory | None = None,
        working: WorkingMemory | None = None,
        repository: MemoryRepository | None = None,
    ) -> None:
        # 四类记忆按类型注册，调用方可以按需启用或替换实现。
        self._memories: dict[MemoryType, Any] = {
            MemoryType.WORKING: working,
            MemoryType.EPISODIC: episodic,
            MemoryType.SEMANTIC: semantic,
            MemoryType.PERCEPTUAL: perceptual,
        }
        # 长期记忆写入必须由治理后的候选进入该事实仓储。
        self._repository = repository

    def _get(self, memory_type: MemoryType) -> Any:
        """获取指定类型的实现；未启用时给出清晰错误。"""
        memory = self._memories.get(memory_type)
        if memory is None:
            raise RuntimeError(f"记忆类型 {memory_type.value} 尚未启用")
        return memory

    def _require_repository(self) -> MemoryRepository:
        """获取长期记忆事实仓储；未组装完成时不要静默返回空结果。"""
        if self._repository is None:
            raise RuntimeError("MemoryManager 未配置长期记忆事实仓储")
        return self._repository

    def _get_long_term(self, memory_type: MemoryType) -> Any:
        """获取可维护的长期记忆；Working Memory 不属于事实维护接口。"""
        if memory_type is MemoryType.WORKING:
            raise ValueError(
                "Working Memory 由 AgentState.messages + Checkpointer 管理，"
                "不能通过长期记忆维护接口操作"
            )
        return self._get(memory_type)

    async def add(self, governed: GovernedCandidate) -> MemoryWriteResult:
        """唯一长期记忆写入口，只接受已经通过 Governance 的候选。"""
        repository = self._require_repository()
        request = governed.request
        if request.memory_type is not governed.candidate.memory_type:
            raise ValueError("治理候选与写入请求的 memory_type 不一致")
        result = await repository.write_managed(request)
        if result.action is not MemoryDecisionAction.DUPLICATE:
            try:
                await self._get_long_term(request.memory_type)._sync_write_result(result)
            except Exception:
                # PostgreSQL 事实已经提交；投影或投影状态失败不能把事实写入误报为失败。
                logger.exception(
                    "长期记忆事实已保存，但检索投影同步异常：memory_id=%s",
                    result.record.memory_id,
                )
        return result

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
        asset_ids: list[str] | None = None,
    ) -> list[MemorySearchResult]:
        """检索单一记忆类型；各类型在内部执行自己的混合召回策略。"""
        kwargs = {
            "user_id": user_id,
            "query": query,
            "limit": limit,
            "conversation_id": conversation_id,
            "project_id": project_id,
            "modality": modality if memory_type is MemoryType.PERCEPTUAL else None,
        }
        if memory_type is MemoryType.PERCEPTUAL:
            kwargs["asset_ids"] = asset_ids
        return await self._get(memory_type).search(**kwargs)

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
        asset_ids: list[str] | None = None,
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
                    asset_ids=(
                        asset_ids if memory_type is MemoryType.PERCEPTUAL else None
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

    async def forget(self, memory_id: str, user_id: str) -> bool:
        """按事实记录中的真实类型遗忘记忆，并清理对应投影。"""
        record = await self._require_repository().get(memory_id, user_id)
        if record is None:
            return False
        return await self._get_long_term(record.memory_type)._forget(memory_id, user_id)

    async def update(
        self,
        memory_id: str,
        user_id: str,
        *,
        importance: float | None = None,
        confidence: float | None = None,
        expires_at: datetime | None = None,
    ) -> MemoryRecord | None:
        """更新记忆治理字段；正文变化必须重新经过 Formation 和版本替换。"""
        record = await self._require_repository().get(memory_id, user_id)
        if record is None:
            return None
        return await self._get_long_term(record.memory_type)._update(
            memory_id,
            user_id,
            importance=importance,
            confidence=confidence,
            expires_at=expires_at,
        )

    async def register_asset(self, asset: MemoryAsset) -> MemoryAsset:
        """登记感知附件；当前只有文本内容可转为记忆。"""
        perceptual = self._get(MemoryType.PERCEPTUAL)
        return await perceptual._register_asset(asset)

    async def get_asset(self, asset_id: str, user_id: str) -> MemoryAsset | None:
        """读取附件元数据，供 ContextEngine 的历史附件引用解析使用。"""
        return await self._require_repository().get_asset(asset_id, user_id)

    async def load_working(
        self,
        *,
        user_id: str,
        conversation_id: str,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        """读取当前线程的 Working Memory，供 ContextEngine 编译短期上下文。"""
        return await self._get(MemoryType.WORKING).load(
            user_id=user_id,
            conversation_id=conversation_id,
            limit=limit,
        )

    async def get_sources(
        self, memory_id: str, user_id: str
    ) -> list[MemorySource]:
        """读取长期记忆的真实来源，供 ContextEngine 生成引用和 trace。"""
        return await self._require_repository().list_sources(memory_id, user_id)
