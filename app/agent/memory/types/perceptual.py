"""Perceptual Memory：当前支持文本，预留其他模态。"""

from dataclasses import replace
from typing import Any
from uuid import uuid4

from app.agent.memory.enums import MemoryScope, MemoryType
from app.agent.memory.interfaces import MemoryAsset, MemoryCreate, MemorySource
from app.agent.memory.types.base import PersistentMemory


class PerceptualMemory(PersistentMemory):
    """保存附件元数据，并把已提取文本作为感知记忆进行索引。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(memory_type=MemoryType.PERCEPTUAL, **kwargs)

    async def add_text(
        self,
        *,
        user_id: str,
        content: str,
        conversation_id: str | None = None,
        project_id: str | None = None,
        asset_id: str | None = None,
        file_name: str = "inline-text.txt",
        storage_uri: str = "inline://text",
        importance: float = 0.5,
        confidence: float = 0.8,
        metadata: dict[str, Any] | None = None,
    ):
        """注册文本附件，并创建对应的 Perceptual Memory。"""
        if not content.strip():
            raise ValueError("文本附件内容不能为空")
        asset = MemoryAsset(
            asset_id=asset_id or str(uuid4()),
            user_id=user_id,
            conversation_id=conversation_id,
            project_id=project_id,
            modality="text",
            file_name=file_name,
            mime_type="text/plain",
            storage_uri=storage_uri,
            extracted_text=content,
            extraction_status="completed",
            index_status="pending",
            metadata=metadata or {},
        )
        await self.repository.save_asset(asset)
        request = MemoryCreate(
            user_id=user_id,
            memory_type=MemoryType.PERCEPTUAL,
            content=content,
            scope=(
                MemoryScope.PROJECT
                if project_id
                else MemoryScope.CONVERSATION
                if conversation_id
                else MemoryScope.USER
            ),
            conversation_id=conversation_id,
            project_id=project_id,
            structured_data={
                "asset_id": asset.asset_id,
                "modality": "text",
            },
            importance=importance,
            confidence=confidence,
        )
        request = replace(
            request,
            sources=(MemorySource("asset", asset.asset_id),),
        )
        try:
            record, indexed = await self.add_with_index_status(request)
        except Exception:
            await self.repository.update_asset(
                asset.asset_id,
                user_id,
                index_status="failed",
            )
            raise
        await self.repository.update_asset(
            asset.asset_id,
            user_id,
            encoder_name=getattr(self.encoder, "name", None),
            embedding_dimension=getattr(self.encoder, "dimension", None),
            index_status=(
                "completed"
                if indexed
                else "failed"
                if self.vector_repository is not None and self.encoder is not None
                else "pending"
            ),
        )
        return record

    async def register_asset(self, asset: MemoryAsset) -> MemoryAsset:
        """登记附件元数据；未实现模态保持 pending/unsupported，不伪造向量。"""
        if asset.modality != "text":
            if asset.extraction_status == "completed":
                raise ValueError("当前只有 text 模态支持内容提取")
        return await self.repository.save_asset(asset)
