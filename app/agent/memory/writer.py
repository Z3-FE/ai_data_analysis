"""把通过治理的记忆候选写入已有 MemoryManager。"""

import hashlib

from app.agent.memory.contracts import GovernedCandidate, MemoryDecision
from app.agent.memory.enums import MemoryDecisionAction
from app.agent.memory.manager import MemoryManager


class MemoryWriter:
    """执行 create、duplicate 和 replace，并返回紧凑审计决定。"""

    def __init__(self, manager: MemoryManager) -> None:
        self.manager = manager

    async def write(
        self,
        governed: GovernedCandidate,
    ) -> MemoryDecision:
        """通过唯一门面提交已治理候选，并转换成紧凑审计决定。"""
        candidate = governed.candidate
        content_hash = hashlib.sha256(
            candidate.content.strip().encode("utf-8")
        ).hexdigest()
        result = await self.manager.add(governed)
        identity_key = str(
            governed.request.structured_data.get("fact_key")
            or governed.request.structured_data.get("event_key")
            or governed.request.structured_data.get("asset_id")
            or ""
        )
        reasons = {
            MemoryDecisionAction.CREATED: "当前类型和作用域中没有相同身份的 active 记忆。",
            MemoryDecisionAction.REPLACED: "相同身份的记忆内容发生变化，已保留旧版本并创建新版本。",
            MemoryDecisionAction.DUPLICATE: "当前类型和作用域中已存在相同身份和正文的 active 记忆。",
        }
        return MemoryDecision(
            candidate_id=candidate.candidate_id,
            memory_type=candidate.memory_type,
            action=result.action,
            reason=reasons[result.action],
            memory_id=result.record.memory_id,
            replaced_memory_id=(
                result.replaced_record.memory_id
                if result.replaced_record is not None
                else None
            ),
            fact_key=candidate.fact_key,
            identity_key=identity_key,
            content_hash=content_hash,
        )


__all__ = ["MemoryWriter"]
