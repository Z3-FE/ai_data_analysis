"""从已经登记的附件文本形成 Perceptual Memory 候选。"""

from app.agent.memory.contracts import MemoryCandidate, TurnMemoryInput
from app.agent.memory.enums import MemoryScope, MemoryType
from app.agent.memory.interfaces import MemoryRepository


class PerceptualMemoryExtractor:
    """读取本轮真实附件元数据，不直接读取客户端提交的文件路径。"""

    def __init__(
        self, repository: MemoryRepository, *, max_content_length: int = 4000
    ) -> None:
        self.repository = repository
        self.max_content_length = max(1, max_content_length)

    async def extract(self, turn: TurnMemoryInput) -> list[MemoryCandidate]:
        """为本轮有已提取文本的附件生成感知记忆候选。"""
        candidates: list[MemoryCandidate] = []
        for asset_id in turn.asset_ids:
            asset = await self.repository.get_asset(asset_id, turn.user_id)
            if asset is None or asset.extraction_status != "completed":
                continue
            content = (asset.extracted_text or "").strip()
            if not content:
                continue
            scope = (
                MemoryScope.PROJECT
                if asset.project_id
                else MemoryScope.CONVERSATION
                if asset.conversation_id
                else MemoryScope.USER
            )
            candidates.append(
                MemoryCandidate(
                    memory_type=MemoryType.PERCEPTUAL,
                    content=content[: self.max_content_length],
                    scope=scope,
                    value={"asset_id": asset.asset_id},
                    structured_data={
                        "asset_id": asset.asset_id,
                        "modality": asset.modality,
                        "truncated": len(content) > self.max_content_length,
                    },
                    importance=0.6,
                    confidence=0.95,
                    reason="本轮附件已有服务端提取文本，可供后续引用。",
                    source_refs=[
                        {
                            "source_type": "asset",
                            "source_id": asset.asset_id,
                        }
                    ],
                )
            )
        return candidates


__all__ = ["PerceptualMemoryExtractor"]
