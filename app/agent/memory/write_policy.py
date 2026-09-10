"""三类长期记忆的幂等与版本替换身份策略。"""

from dataclasses import dataclass, field
from typing import Any

from app.agent.memory.enums import MemoryType
from app.agent.memory.interfaces import MemoryCreate


@dataclass(frozen=True, slots=True)
class MemoryWriteIdentity:
    """一次写入在用户、类型和作用域之内的逻辑身份。"""

    # 身份策略名称，便于日志和测试区分三种语义。
    identity_type: str
    # 可更新事实、历史事件或附件的稳定键。
    identity_key: str
    # 身份附加条件；Semantic 使用事实适用条件，Perceptual 使用模态。
    qualifiers: dict[str, Any] = field(default_factory=dict)


def resolve_memory_write_identity(request: MemoryCreate) -> MemoryWriteIdentity:
    """根据记忆类型选择身份，禁止三类记忆共用事实覆盖规则。"""
    data = request.structured_data
    if request.memory_type is MemoryType.SEMANTIC:
        fact_key = str(data.get("fact_key") or "").strip()
        conditions = data.get("conditions", {})
        if not isinstance(conditions, dict):
            conditions = {}
        if fact_key:
            return MemoryWriteIdentity(
                identity_type="semantic_fact",
                identity_key=fact_key,
                qualifiers={"conditions": conditions},
            )
        # 无稳定键的语义原文只能精确去重，不能覆盖另一条事实。
        return MemoryWriteIdentity(
            identity_type="semantic_content",
            identity_key=request.content.strip(),
        )

    if request.memory_type is MemoryType.EPISODIC:
        event_key = str(data.get("event_key") or "").strip()
        if not event_key:
            raise ValueError("Episodic Memory 必须提供经过治理的 event_key")
        # 不同 event_key 始终追加；同一事件重放或修正时幂等/创建新版本。
        return MemoryWriteIdentity("episodic_event", event_key)

    if request.memory_type is MemoryType.PERCEPTUAL:
        asset_id = str(data.get("asset_id") or "").strip()
        modality = str(data.get("modality") or "").strip()
        if not asset_id or not modality:
            raise ValueError("Perceptual Memory 必须提供 asset_id 和 modality")
        return MemoryWriteIdentity(
            identity_type="perceptual_asset",
            identity_key=asset_id,
            qualifiers={"modality": modality},
        )

    raise ValueError("Working Memory 不能写入长期记忆事实表")


__all__ = ["MemoryWriteIdentity", "resolve_memory_write_identity"]
