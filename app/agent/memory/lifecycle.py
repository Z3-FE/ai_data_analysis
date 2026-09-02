"""记忆生命周期维护：过期处理和显式索引重试。"""

from typing import Any

from app.agent.memory.enums import MemoryStatus, MemoryType
from app.agent.memory.types.semantic import SemanticMemory


class MemoryLifecycle:
    """将清理和索引修复从在线请求中分离出来。"""

    def __init__(
        self,
        *,
        repository: Any,
        memories: dict[MemoryType, Any],
        max_retry_attempts: int = 5,
    ) -> None:
        # 生命周期任务使用同一个 PostgreSQL 事实仓储。
        self.repository = repository
        # 已启用的持久化记忆类型，用于按类型重建索引。
        self.memories = memories
        # 防止永久故障任务在每次维护中无限重试。
        self.max_retry_attempts = max(1, max_retry_attempts)

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
                if memory.vector_repository is None:
                    await self.repository.enqueue_index_job(record.memory_id, "qdrant")
                else:
                    try:
                        await memory.vector_repository.delete(record)
                    except Exception as exc:
                        await self.repository.enqueue_index_job(
                            record.memory_id, "qdrant", str(exc)
                        )
                if isinstance(memory, SemanticMemory):
                    if memory.graph_repository is None:
                        await self.repository.enqueue_index_job(
                            record.memory_id, "neo4j"
                        )
                    else:
                        try:
                            await memory.graph_repository.delete_projection(
                                record.memory_id
                            )
                        except Exception as exc:
                            await self.repository.enqueue_index_job(
                                record.memory_id, "neo4j", str(exc)
                            )
                changed += 1
        return changed

    async def rebuild_pending_graph(self, limit: int = 100) -> dict[str, int]:
        """把 PostgreSQL 中 pending 的 Semantic 图投影写入新启用的 Neo4j。"""
        semantic = self.memories.get(MemoryType.SEMANTIC)
        if (
            not isinstance(semantic, SemanticMemory)
            or semantic.graph_repository is None
        ):
            return {"completed": 0, "failed": 0}
        projections = await self.repository.list_graph_projections(
            sync_status="pending",
            limit=limit,
        )
        completed = 0
        failed = 0
        for projection in projections:
            memory_id = projection["memory_id"]
            record = await self.repository.get_by_id(memory_id)
            if record is None or record.status is not MemoryStatus.ACTIVE:
                try:
                    if record is not None:
                        await semantic.graph_repository.delete_projection(memory_id)
                    await self.repository.update_graph_projection(
                        memory_id, "completed"
                    )
                    completed += 1
                except Exception as exc:
                    await self.repository.update_graph_projection(
                        memory_id, "failed", str(exc)
                    )
                    await self.repository.enqueue_index_job(
                        memory_id, "neo4j", str(exc)
                    )
                    failed += 1
                continue
            try:
                await semantic.graph_repository.upsert_projection(
                    record,
                    projection["entities"],
                    projection["relations"],
                )
                await self.repository.update_graph_projection(memory_id, "completed")
                completed += 1
            except Exception as exc:
                await self.repository.update_graph_projection(
                    memory_id, "failed", str(exc)
                )
                await self.repository.enqueue_index_job(memory_id, "neo4j", str(exc))
                failed += 1
        return {"completed": completed, "failed": failed}

    async def retry_indexes(self, limit: int = 100) -> dict[str, int]:
        """重试失败的 Qdrant/Neo4j 索引任务。"""
        jobs = await self.repository.list_index_jobs(
            limit,
            max_attempts=self.max_retry_attempts,
        )
        completed = 0
        failed = 0
        for job in jobs:
            record = await self.repository.get_by_id(job["memory_id"])
            memory = self.memories.get(record.memory_type) if record else None
            # 依赖尚未启用时保留当前状态，不空耗重试预算。
            if record is not None and memory is not None:
                if job["target"] == "qdrant" and (
                    memory.vector_repository is None or memory.encoder is None
                ):
                    continue
                if (
                    job["target"] == "neo4j"
                    and isinstance(memory, SemanticMemory)
                    and memory.graph_repository is None
                ):
                    continue
            await self.repository.update_index_job(job["job_id"], "processing")
            try:
                if record is None or memory is None:
                    raise RuntimeError("索引任务对应的记忆不存在或类型未启用")
                if record.status is not MemoryStatus.ACTIVE:
                    # 记忆在索引失败后可能已经被遗忘或过期，不能把旧内容重新写回索引。
                    if (
                        job["target"] == "qdrant"
                        and memory.vector_repository is not None
                    ):
                        await memory.vector_repository.delete(record)
                    if (
                        job["target"] == "neo4j"
                        and isinstance(memory, SemanticMemory)
                        and memory.graph_repository is not None
                    ):
                        await memory.graph_repository.delete_projection(
                            record.memory_id
                        )
                    await self.repository.update_index_job(job["job_id"], "completed")
                    completed += 1
                    continue
                if job["target"] == "qdrant":
                    if not await memory.index_record(record):
                        raise RuntimeError("Qdrant 索引同步失败")
                    if record.memory_type is MemoryType.PERCEPTUAL:
                        asset_id = str(record.structured_data.get("asset_id") or "")
                        if asset_id:
                            await self.repository.update_asset(
                                asset_id,
                                record.user_id,
                                encoder_name=getattr(memory.encoder, "name", None),
                                embedding_dimension=getattr(
                                    memory.encoder, "dimension", None
                                ),
                                index_status="completed",
                            )
                elif job["target"] == "neo4j" and isinstance(memory, SemanticMemory):
                    if not await memory.sync_graph_record(record):
                        raise RuntimeError("Neo4j 图投影同步失败")
                else:
                    raise RuntimeError(f"不支持的索引目标：{job['target']}")
                await self.repository.update_index_job(job["job_id"], "completed")
                completed += 1
            except Exception as exc:
                if (
                    record is not None
                    and record.memory_type is MemoryType.PERCEPTUAL
                    and job["target"] == "qdrant"
                ):
                    asset_id = str(record.structured_data.get("asset_id") or "")
                    if asset_id:
                        await self.repository.update_asset(
                            asset_id,
                            record.user_id,
                            index_status="failed",
                        )
                await self.repository.update_index_job(
                    job["job_id"], "failed", str(exc)
                )
                failed += 1
        return {"completed": completed, "failed": failed}
