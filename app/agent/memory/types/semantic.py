"""Semantic Memory：保存稳定事实、偏好、规则和结构化关系。"""

import logging
from typing import Any

from app.agent.memory.enums import MemoryType
from app.agent.memory.graph.extractor import GraphExtractor
from app.agent.memory.interfaces import (
    GraphMemoryRepository,
    MemoryCreate,
    MemoryRecord,
)
from app.agent.memory.types.base import PersistentMemory, is_memory_visible

logger = logging.getLogger(__name__)


class SemanticMemory(PersistentMemory):
    """使用 Qdrant 检索、并把明确结构化关系投影到 Neo4j 的语义记忆。"""

    def __init__(
        self, *, graph_repository: GraphMemoryRepository | None = None, **kwargs: Any
    ) -> None:
        super().__init__(memory_type=MemoryType.SEMANTIC, **kwargs)
        # Neo4j 是语义记忆的关系投影，不是唯一事实来源。
        self.graph_repository = graph_repository
        # 当前只接受调用方明确提供的实体和关系，不从自然语言猜关系。
        self.graph_extractor = GraphExtractor()

    async def add(self, request: MemoryCreate) -> MemoryRecord:
        """保存语义事实，并尽力同步图投影。"""
        record = await super().add(request)
        await self._sync_graph(record)
        return record

    async def replace(
        self, memory_id: str, user_id: str, request: MemoryCreate
    ) -> MemoryRecord:
        """替换语义事实版本，并把图投影切换到新版本。"""
        record = await super().replace(memory_id, user_id, request)
        if self.graph_repository is None:
            await self.repository.enqueue_index_job(memory_id, "neo4j")
        else:
            try:
                await self.graph_repository.delete_projection(memory_id)
            except Exception as exc:
                await self.repository.enqueue_index_job(memory_id, "neo4j", str(exc))
        await self._sync_graph(record)
        return record

    async def forget(self, memory_id: str, user_id: str) -> bool:
        """遗忘语义事实时删除 Neo4j 投影。"""
        changed = await super().forget(memory_id, user_id)
        if changed:
            if self.graph_repository is None:
                await self.repository.enqueue_index_job(memory_id, "neo4j")
            else:
                try:
                    await self.graph_repository.delete_projection(memory_id)
                except Exception as exc:
                    await self.repository.enqueue_index_job(
                        memory_id, "neo4j", str(exc)
                    )
        return changed

    async def search(self, **kwargs: Any):
        """合并向量召回和实体命中结果，并去重。"""
        limit = kwargs.get("limit", 5)
        if limit <= 0:
            return []
        base_results = await super().search(**kwargs)
        if self.graph_repository is None:
            return base_results

        try:
            graph_items = await self.graph_repository.search(
                user_id=kwargs["user_id"],
                query=kwargs["query"],
                limit=max(limit * 2, limit),
                conversation_id=kwargs.get("conversation_id"),
                project_id=kwargs.get("project_id"),
            )
        except Exception:
            logger.exception("Neo4j Semantic Memory 检索失败，返回向量或词法结果")
            return base_results
        known = {item.memory.memory_id for item in base_results}
        base_memory_ids = {item.memory.memory_id for item in base_results}
        for item in graph_items:
            memory_id = str(item.get("memory_id", ""))
            if not memory_id or memory_id in known:
                continue
            record = await self.repository.get(memory_id, kwargs["user_id"])
            if record is not None and is_memory_visible(
                record,
                memory_type=MemoryType.SEMANTIC,
                conversation_id=kwargs.get("conversation_id"),
                project_id=kwargs.get("project_id"),
            ):
                ranked = self.ranker.rank(
                    [(record, float(item.get("score", 0.8)), "neo4j")], 1
                )
                if ranked:
                    base_results.append(ranked[0])
                    known.add(memory_id)
        base_results.sort(key=lambda item: item.score, reverse=True)
        selected = base_results[:limit]
        await self.repository.touch_access(
            [
                item.memory.memory_id
                for item in selected
                if item.memory.memory_id not in base_memory_ids
            ],
            kwargs["user_id"],
        )
        return selected

    async def _sync_graph(self, record: MemoryRecord) -> bool:
        """把结构化实体关系保存到 PostgreSQL，并尽力写入 Neo4j。"""
        entities, relations = self.graph_extractor.extract(record)
        await self.repository.save_graph_projection(
            record,
            entities,
            relations,
            sync_status="pending",
        )
        if self.graph_repository is None:
            # PostgreSQL 已保存重建输入，但还没有真正写入图数据库。
            return False
        try:
            await self.graph_repository.upsert_projection(record, entities, relations)
            await self.repository.update_graph_projection(record.memory_id, "completed")
            return True
        except Exception as exc:
            await self.repository.update_graph_projection(
                record.memory_id,
                "failed",
                str(exc),
            )
            await self.repository.enqueue_index_job(record.memory_id, "neo4j", str(exc))
            return False

    async def sync_graph_record(self, record: MemoryRecord) -> bool:
        """公开一次图投影同步，供生命周期维护器调用。"""
        return await self._sync_graph(record)
