"""长期记忆候选的后端治理。

提取器负责提出候选，治理层负责决定候选是否有资格进入记忆仓储。
这里是安全边界：不信任 LLM 传入的身份、来源、memory_id、作用域和敏感内容。
"""

import hashlib
import json
import re

from app.agent.memory.contracts import (
    GovernedCandidate,
    MemoryCandidate,
    TurnMemoryInput,
)
from app.agent.memory.enums import MemoryScope, MemoryType
from app.agent.memory.interfaces import MemoryCreate, MemoryRepository, MemorySource

_ALLOWED_TYPES = {MemoryType.SEMANTIC, MemoryType.EPISODIC, MemoryType.PERCEPTUAL}
_ALLOWED_SOURCE_TYPES = {"turn", "message", "output", "asset"}
_SENSITIVE_PATTERNS = (
    re.compile(r"\b(?:password|passwd|api[ _-]?key|access[ _-]?token|secret)\b", re.I),
    re.compile(r"\b(?:private[ _-]?key|recovery[ _-]?code|refresh[ _-]?token)\b", re.I),
    re.compile(r"(?:sk|rk)-[A-Za-z0-9_-]{16,}"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"(?:密码|口令|API[ _-]?密钥|访问令牌|刷新令牌|私钥|恢复码)", re.I),
)


class MemoryGovernanceError(ValueError):
    """候选未通过长期记忆写入治理。"""


class MemoryGovernance:
    """把不可信候选转换为可写入的、带真实来源的 MemoryCreate。"""

    def __init__(
        self,
        repository: MemoryRepository,
        *,
        max_content_length: int = 4000,
        min_confidence: float = 0.6,
    ) -> None:
        self.repository = repository
        self.max_content_length = max(1, max_content_length)
        self.min_confidence = min(max(min_confidence, 0.0), 1.0)

    async def govern(
        self, candidate: MemoryCandidate, turn: TurnMemoryInput
    ) -> GovernedCandidate:
        """校验候选并使用服务端轮次身份重建写入请求。"""
        self._validate_candidate(candidate)
        self._validate_sensitive_content(candidate)
        if candidate.memory_type is MemoryType.PERCEPTUAL and not turn.asset_ids:
            raise MemoryGovernanceError("Perceptual Memory 必须绑定本轮真实附件。")
        await self._validate_declared_sources(candidate, turn)

        scope = self._resolve_scope(candidate, turn)
        sources = await self._real_sources(candidate, turn)
        structured_data = dict(candidate.structured_data)
        if candidate.memory_type is MemoryType.SEMANTIC:
            # Semantic 使用稳定事实键和条件决定是否形成新版本。
            structured_data["fact_key"] = candidate.fact_key.strip()
        elif candidate.memory_type is MemoryType.EPISODIC:
            # 没有显式事件键时，以真实轮次和正文生成可重放的事件身份。
            content_hash = hashlib.sha256(
                candidate.content.strip().encode("utf-8")
            ).hexdigest()[:24]
            structured_data["event_key"] = (
                candidate.event_key.strip() or f"{turn.turn_id}:{content_hash}"
            )
        structured_data["value"] = candidate.value
        structured_data["conditions"] = dict(candidate.conditions)
        request = MemoryCreate(
            user_id=turn.user_id,
            memory_type=candidate.memory_type,
            content=candidate.content.strip()[: self.max_content_length],
            scope=scope,
            conversation_id=(
                turn.conversation_id if scope is MemoryScope.CONVERSATION else None
            ),
            project_id=turn.project_id if scope is MemoryScope.PROJECT else None,
            structured_data=structured_data,
            importance=candidate.importance,
            confidence=candidate.confidence,
            sources=sources,
        )
        return GovernedCandidate(candidate=candidate, request=request)

    def _validate_candidate(self, candidate: MemoryCandidate) -> None:
        if candidate.memory_type not in _ALLOWED_TYPES:
            raise MemoryGovernanceError(
                f"不允许形成 {candidate.memory_type.value} 类型的长期记忆。"
            )
        if len(candidate.content.strip()) > self.max_content_length:
            raise MemoryGovernanceError("候选内容超过长期记忆长度限制。")
        if not candidate.content.strip():
            raise MemoryGovernanceError("候选内容不能为空。")
        if not 0 <= candidate.importance <= 1 or not 0 <= candidate.confidence <= 1:
            raise MemoryGovernanceError("importance 和 confidence 必须在 0 到 1 之间。")
        if candidate.confidence < self.min_confidence:
            raise MemoryGovernanceError(
                f"候选置信度低于长期记忆阈值 {self.min_confidence:g}。"
            )
        try:
            json.dumps(
                {
                    "value": candidate.value,
                    "conditions": candidate.conditions,
                    "structured_data": candidate.structured_data,
                },
                ensure_ascii=False,
            )
        except (TypeError, ValueError) as exc:
            raise MemoryGovernanceError("候选结构化数据无法保存为 JSON。") from exc

    @staticmethod
    def _validate_sensitive_content(candidate: MemoryCandidate) -> None:
        raw = json.dumps(
            {
                "content": candidate.content,
                "value": candidate.value,
                "conditions": candidate.conditions,
                "structured_data": candidate.structured_data,
            },
            ensure_ascii=False,
            default=str,
        )
        if any(pattern.search(raw) for pattern in _SENSITIVE_PATTERNS):
            raise MemoryGovernanceError("候选包含密码、密钥、令牌或恢复码等敏感信息。")

    async def _validate_declared_sources(
        self, candidate: MemoryCandidate, turn: TurnMemoryInput
    ) -> None:
        for source in candidate.source_refs:
            source_type = str(source.get("source_type") or source.get("type") or "")
            source_id = str(source.get("source_id") or source.get("id") or "")
            if source_type not in _ALLOWED_SOURCE_TYPES or not source_id:
                raise MemoryGovernanceError("候选声明了无效的来源引用。")
            if not await self.repository.source_exists(
                user_id=turn.user_id,
                source_type=source_type,
                source_id=source_id,
                conversation_id=turn.conversation_id,
            ):
                raise MemoryGovernanceError("候选来源不属于当前用户或当前会话。")

    @staticmethod
    def _resolve_scope(
        candidate: MemoryCandidate, turn: TurnMemoryInput
    ) -> MemoryScope:
        """把候选作用域限制在当前真实身份可以支撑的范围内。"""
        if candidate.scope is MemoryScope.PROJECT:
            if not turn.project_id:
                raise MemoryGovernanceError("项目级记忆缺少服务端 project_id。")
            return MemoryScope.PROJECT
        if candidate.scope is MemoryScope.CONVERSATION:
            return MemoryScope.CONVERSATION
        return MemoryScope.USER

    async def _real_sources(
        self,
        candidate: MemoryCandidate,
        turn: TurnMemoryInput,
    ) -> tuple[MemorySource, ...]:
        """用服务端真实轮次和附件 ID覆盖提取器声称的来源。"""
        if not await self.repository.source_exists(
            user_id=turn.user_id,
            source_type="turn",
            source_id=turn.turn_id,
            conversation_id=turn.conversation_id,
        ):
            raise MemoryGovernanceError("本轮会话来源不存在或不属于当前用户。")
        sources = [MemorySource("turn", turn.turn_id)]
        asset_ids: tuple[str, ...] = ()
        if candidate.memory_type is MemoryType.PERCEPTUAL:
            asset_id = str(candidate.structured_data.get("asset_id") or "").strip()
            if not asset_id or asset_id not in turn.asset_ids:
                raise MemoryGovernanceError(
                    "Perceptual Memory 未绑定本轮真实附件 ID。"
                )
            asset_ids = (asset_id,)
        for asset_id in asset_ids:
            if not await self.repository.source_exists(
                user_id=turn.user_id,
                source_type="asset",
                source_id=asset_id,
                conversation_id=turn.conversation_id,
            ):
                raise MemoryGovernanceError("本轮附件不属于当前用户或当前会话。")
            sources.append(MemorySource("asset", asset_id))
        return tuple(sources)


__all__ = ["MemoryGovernance", "MemoryGovernanceError"]
