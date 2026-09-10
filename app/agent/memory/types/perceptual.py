"""Perceptual Memory：当前支持文本，预留其他模态。"""

from typing import Any

from app.agent.memory.enums import MemoryType
from app.agent.memory.interfaces import MemoryAsset, MemoryWriteResult
from app.agent.memory.types.base import PersistentMemory


class PerceptualMemory(PersistentMemory):
    """保存附件元数据，并把已提取文本作为感知记忆进行索引。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(memory_type=MemoryType.PERCEPTUAL, **kwargs)

    async def _register_asset(self, asset: MemoryAsset) -> MemoryAsset:
        """登记附件元数据；未实现模态保持 pending/unsupported，不伪造向量。"""
        if asset.modality != "text":
            if asset.extraction_status == "completed":
                raise ValueError("当前只有 text 模态支持内容提取")
        return await self.repository.save_asset(asset)

    async def _sync_write_result(self, result: MemoryWriteResult) -> bool:
        """写入文本向量后回写附件索引状态，保持两套事实一致。"""
        indexed = await super()._sync_write_result(result)
        asset_id = str(result.record.structured_data.get("asset_id") or "").strip()
        if not asset_id:
            return indexed

        if indexed:
            index_status = "completed"
            encoder_name = getattr(self.encoder, "name", None)
            embedding_dimension = getattr(self.encoder, "dimension", None)
        elif self.vector_repository is not None and self.encoder is not None:
            # 已启用向量依赖但本次同步失败，失败表负责后续处理。
            index_status = "failed"
            encoder_name = None
            embedding_dimension = None
        else:
            # 当前没有向量投影配置，不把“未启用”伪装成“已完成”。
            index_status = "pending"
            encoder_name = None
            embedding_dimension = None

        await self.repository.update_asset(
            asset_id,
            result.record.user_id,
            index_status=index_status,
            encoder_name=encoder_name,
            embedding_dimension=embedding_dimension,
        )
        return indexed
