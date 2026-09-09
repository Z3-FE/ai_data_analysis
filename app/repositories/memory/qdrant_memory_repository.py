"""记忆专用 Qdrant 向量仓储。"""

import asyncio
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    MatchValue,
    PayloadSchemaType,
    PointIdsList,
    PointStruct,
    VectorParams,
)

from app.agent.memory.enums import MemoryStatus, MemoryType
from app.agent.memory.interfaces import MemoryRecord
from app.core.config import QdrantConfig


def _distance(name: str) -> Distance:
    """把配置中的距离名称转换为 Qdrant 枚举。"""
    try:
        return Distance[name.upper()]
    except KeyError as exc:
        raise ValueError(f"不支持的 Qdrant 距离：{name}") from exc


def _point_id(memory_id: str) -> str:
    """把任意业务记忆 ID 映射为 Qdrant 支持的稳定 UUID。"""
    return str(uuid5(NAMESPACE_URL, f"agent-memory:{memory_id}"))


class QdrantMemoryRepository:
    """按记忆类型使用隔离 collection 的 Qdrant 仓储。"""

    def __init__(self, client: AsyncQdrantClient, config: QdrantConfig) -> None:
        # 复用应用生命周期创建的异步 Qdrant 客户端。
        self.client = client
        # 保存 collection 名称、维度和批量配置。
        self.config = config
        # 避免每次写入都重复检查 collection 和 payload 索引。
        self._ready_collections: set[str] = set()
        # 同一进程并发首次写入时，只允许一个协程初始化指定 collection。
        self._collection_locks: dict[str, asyncio.Lock] = {}

    def collection_for(self, memory_type: MemoryType, modality: str = "text") -> str:
        """返回一种记忆对应的 Qdrant collection。"""
        if memory_type is MemoryType.EPISODIC:
            return self.config.memory_episodic_collection
        if memory_type is MemoryType.SEMANTIC:
            return self.config.memory_semantic_collection
        if memory_type is MemoryType.PERCEPTUAL:
            collections = {
                "text": self.config.memory_perceptual_text_collection,
                "image": self.config.memory_perceptual_image_collection,
                "audio": self.config.memory_perceptual_audio_collection,
                "video": self.config.memory_perceptual_video_collection,
            }
            try:
                return collections[modality]
            except KeyError as exc:
                raise ValueError(f"不支持的感知模态：{modality}") from exc
        raise ValueError("Working Memory 不使用 Qdrant")

    async def ensure_collection(
        self, memory_type: MemoryType, modality: str = "text"
    ) -> None:
        """按需创建 collection 和过滤字段索引。"""
        collection_name = self.collection_for(memory_type, modality)
        if collection_name in self._ready_collections:
            return
        lock = self._collection_locks.setdefault(collection_name, asyncio.Lock())
        async with lock:
            if collection_name in self._ready_collections:
                return
            if not await self.client.collection_exists(collection_name):
                try:
                    await self.client.create_collection(
                        collection_name=collection_name,
                        vectors_config=VectorParams(
                            size=self.config.vector_size,
                            distance=_distance(self.config.distance),
                        ),
                    )
                except Exception as exc:
                    # 另一个应用实例可能刚好完成了同名 collection 的创建。
                    if "already exists" not in str(exc).lower():
                        raise
            for field_name in (
                "user_id",
                "memory_type",
                "conversation_id",
                "project_id",
                "scope",
                "status",
                "modality",
                "asset_id",
            ):
                try:
                    await self.client.create_payload_index(
                        collection_name=collection_name,
                        field_name=field_name,
                        field_schema=PayloadSchemaType.KEYWORD,
                        wait=True,
                    )
                except Exception as exc:
                    if "already exists" not in str(exc).lower():
                        raise
            self._ready_collections.add(collection_name)

    async def upsert(self, memory: MemoryRecord, vector: list[float]) -> None:
        """写入一条记忆向量及用于隔离的 payload。"""
        if len(vector) != self.config.vector_size:
            raise ValueError(
                f"记忆向量维度错误：期望 {self.config.vector_size}，实际 {len(vector)}"
            )
        modality = str(memory.structured_data.get("modality", "text"))
        await self.ensure_collection(memory.memory_type, modality)
        collection_name = self.collection_for(memory.memory_type, modality)
        try:
            await self.client.upsert(
                collection_name=collection_name,
                points=[
                    PointStruct(
                        id=_point_id(memory.memory_id),
                        vector=vector,
                        payload={
                            "memory_id": memory.memory_id,
                            "user_id": memory.user_id,
                            "memory_type": memory.memory_type.value,
                            "conversation_id": memory.conversation_id or "",
                            "project_id": memory.project_id or "",
                            "modality": modality,
                            "asset_id": str(memory.structured_data.get("asset_id") or ""),
                            "scope": memory.scope.value,
                            "status": memory.status.value,
                            "version": memory.version,
                        },
                    )
                ],
                wait=True,
            )
        except Exception:
            # collection 被外部重建时，下次重试需要重新执行 Schema 检查。
            self._ready_collections.discard(collection_name)
            raise

    async def search(
        self,
        *,
        user_id: str,
        memory_type: MemoryType,
        vector: list[float],
        limit: int,
        conversation_id: str | None = None,
        project_id: str | None = None,
        modality: str | None = None,
        asset_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """按用户、状态和可见会话范围进行向量检索。"""
        collection_name = self.collection_for(memory_type, modality or "text")
        if not await self.client.collection_exists(collection_name):
            return []
        must = [
            FieldCondition(key="user_id", match=MatchValue(value=user_id)),
            FieldCondition(
                key="memory_type", match=MatchValue(value=memory_type.value)
            ),
            FieldCondition(
                key="status", match=MatchValue(value=MemoryStatus.ACTIVE.value)
            ),
        ]
        # 作用域条件必须作为一个嵌套 OR 放入 must，确保 project/conversation
        # 记忆不会因为 Qdrant 的 should 语义而被错误放行。
        visible_scopes = [FieldCondition(key="scope", match=MatchValue(value="user"))]
        if conversation_id:
            visible_scopes.append(
                Filter(
                    must=[
                        FieldCondition(
                            key="scope", match=MatchValue(value="conversation")
                        ),
                        FieldCondition(
                            key="conversation_id",
                            match=MatchValue(value=conversation_id),
                        ),
                    ]
                )
            )
        if project_id:
            visible_scopes.append(
                Filter(
                    must=[
                        FieldCondition(key="scope", match=MatchValue(value="project")),
                        FieldCondition(
                            key="project_id", match=MatchValue(value=project_id)
                        ),
                    ]
                )
            )
        must.append(Filter(should=visible_scopes))
        if modality:
            must.append(
                FieldCondition(key="modality", match=MatchValue(value=modality))
            )
        if asset_ids is not None:
            if not asset_ids:
                return []
            must.append(
                FieldCondition(
                    key="asset_id",
                    match=MatchAny(any=list(dict.fromkeys(asset_ids))),
                )
            )
        response = await self.client.query_points(
            collection_name=collection_name,
            query=vector,
            query_filter=Filter(must=must),
            limit=max(0, limit),
            with_payload=True,
            with_vectors=False,
        )
        return [
            {
                "memory_id": str(point.payload.get("memory_id", point.id)),
                "score": float(point.score),
            }
            for point in response.points
        ]

    async def delete(self, memory: MemoryRecord) -> None:
        """删除已遗忘记忆的向量投影。"""
        modality = str(memory.structured_data.get("modality", "text"))
        collection_name = self.collection_for(memory.memory_type, modality)
        if await self.client.collection_exists(collection_name):
            await self.client.delete(
                collection_name=collection_name,
                points_selector=PointIdsList(points=[_point_id(memory.memory_id)]),
                wait=True,
            )
