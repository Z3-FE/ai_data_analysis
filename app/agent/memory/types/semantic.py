"""Semantic Memory：保存稳定事实、偏好、规则和结构化关系。"""

import logging
from typing import Any

from app.agent.memory.enums import MemoryType
from app.agent.memory.graph.extractor import GraphExtractor
from app.agent.memory.interfaces import (
    GraphMemoryRepository,
    MemoryRecord,
    MemoryWriteResult,
)
from app.agent.memory.ranker import MemoryRecallCandidate
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

    async def _sync_write_result(self, result: MemoryWriteResult) -> bool:
        """同步原子事实写入后的向量和图投影，并返回向量投影结果。"""
        indexed = await super()._sync_write_result(result)
        if result.replaced_record is not None and self.graph_repository is not None:
            try:
                await self.graph_repository.delete_projection(
                    result.replaced_record.memory_id
                )
            except Exception as exc:
                await self.repository.record_index_failure(
                    result.replaced_record.memory_id,
                    "neo4j",
                    str(exc),
                )
        await self._sync_graph(result.record)
        return indexed

    async def _forget(self, memory_id: str, user_id: str) -> bool:
        """遗忘语义事实时删除 Neo4j 投影。"""
        changed = await super()._forget(memory_id, user_id)
        if changed:
            if self.graph_repository is not None:
                try:
                    await self.graph_repository.delete_projection(memory_id)
                except Exception as exc:
                    await self.repository.record_index_failure(
                        memory_id, "neo4j", str(exc)
                    )
        return changed

    async def search(self, **kwargs: Any):
        """并行融合词法、向量和 Neo4j 关系召回。"""
        limit = kwargs.get("limit", 5)
        if limit <= 0:
            return []
        candidates = await self._recall_candidates(
            user_id=kwargs["user_id"],
            query=kwargs["query"],
            limit=limit,
            conversation_id=kwargs.get("conversation_id"),
            project_id=kwargs.get("project_id"),
            modality=None,
            asset_ids=None,
        )

        if self.graph_repository is not None:
            try:
                graph_items = await self.graph_repository.search(
                    user_id=kwargs["user_id"],
                    query=kwargs["query"],
                    limit=max(limit * 4, limit),
                    conversation_id=kwargs.get("conversation_id"),
                    project_id=kwargs.get("project_id"),
                )
            except Exception:
                logger.exception("Neo4j Semantic Memory 检索失败，继续使用其他召回结果")
                graph_items = []
            for item in graph_items:
                memory_id = str(item.get("memory_id", ""))
                if not memory_id:
                    continue
                record = await self.repository.get(memory_id, kwargs["user_id"])
                if record is None or not is_memory_visible(
                    record,
                    memory_type=MemoryType.SEMANTIC,
                    conversation_id=kwargs.get("conversation_id"),
                    project_id=kwargs.get("project_id"),
                ):
                    continue
                candidate = candidates.setdefault(
                    memory_id, MemoryRecallCandidate(memory=record)
                )
                candidate.add_signal("graph", float(item.get("score", 0.0)))

        selected = self.ranker.rank(list(candidates.values()), limit)
        await self.repository.touch_access(
            [item.memory.memory_id for item in selected],
            kwargs["user_id"],
        )
        return selected

    async def _sync_graph(self, record: MemoryRecord) -> bool:
        """把结构化实体关系保存到 PostgreSQL，并尽力写入 Neo4j。"""
        entities, relations = self.graph_extractor.extract(record)
        if not entities:
            # 没有实体时图检索无法命中，不创建不可检索的空 Memory 节点。
            return False
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
            await self.repository.record_index_failure(record.memory_id, "neo4j", str(exc))
            return False
