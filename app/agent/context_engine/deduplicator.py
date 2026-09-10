"""ContextEngine 候选去重和版本消歧。"""

import json
import re
from dataclasses import dataclass, replace

from app.agent.context_engine.contracts import (
    ContextItem,
    ContextSelectionDecision,
    ContextSourceKind,
)


@dataclass(frozen=True, slots=True)
class DeduplicationResult:
    """去重后候选以及被丢弃项的可审计决定。"""

    items: tuple[ContextItem, ...]
    decisions: tuple[ContextSelectionDecision, ...]


class ContextDeduplicator:
    """先按类型化身份消歧，再合并规范化正文完全重复的候选。"""

    def deduplicate(self, items: list[ContextItem]) -> DeduplicationResult:
        by_identity: dict[str, ContextItem] = {}
        passthrough: list[ContextItem] = []
        decisions: list[ContextSelectionDecision] = []
        for item in items:
            identity = self._identity(item)
            if identity is None:
                passthrough.append(item)
                continue
            previous = by_identity.get(identity)
            if previous is None:
                by_identity[identity] = item
                continue
            winner, loser = self._prefer(previous, item)
            by_identity[identity] = self._merge_refs(winner, loser)
            decisions.append(self._discard(loser, "同一逻辑对象存在更新或更优版本"))

        unique: dict[str, ContextItem] = {}
        for item in [*passthrough, *by_identity.values()]:
            content_key = self._content_identity(item)
            if not content_key:
                decisions.append(self._discard(item, "候选正文为空"))
                continue
            previous = unique.get(content_key)
            if previous is None:
                unique[content_key] = item
                continue
            winner, loser = self._prefer(previous, item)
            unique[content_key] = self._merge_refs(winner, loser)
            decisions.append(self._discard(loser, "正文与另一候选重复"))
        return DeduplicationResult(tuple(unique.values()), tuple(decisions))

    @staticmethod
    def _identity(item: ContextItem) -> str | None:
        data = item.structured_data
        fact_key = str(data.get("fact_key") or "").strip()
        if fact_key:
            conditions = data.get("conditions", {})
            if not isinstance(conditions, dict):
                conditions = {}
            qualifier = json.dumps(conditions, sort_keys=True, ensure_ascii=False)
            return f"semantic:{fact_key}:{qualifier}"
        event_key = str(data.get("event_key") or "").strip()
        if event_key:
            return f"episodic:{event_key}"
        asset_id = str(data.get("asset_id") or "").strip()
        if asset_id:
            modality = str(data.get("modality") or "").strip()
            return f"perceptual:{asset_id}:{modality}"
        return None

    @staticmethod
    def _content_key(content: str) -> str:
        return re.sub(r"\s+", "", content).casefold()

    @classmethod
    def _content_identity(cls, item: ContextItem) -> str:
        """Working 和摘要具有时序语义，不能与其他来源按正文互相替换。"""
        content_key = cls._content_key(item.content)
        if item.source_kind in {ContextSourceKind.WORKING, ContextSourceKind.SUMMARY}:
            return f"{item.source_kind.value}:{item.item_id}:{content_key}"
        return content_key

    @staticmethod
    def _prefer(
        left: ContextItem, right: ContextItem
    ) -> tuple[ContextItem, ContextItem]:
        def quality(
            item: ContextItem,
        ) -> tuple[int, int, int, float, float, float]:
            raw_version = item.structured_data.get("version", 1)
            try:
                version = int(raw_version)
            except (TypeError, ValueError):
                version = 1
            return (
                int(item.required),
                int(bool(item.structured_data.get("direct_asset"))),
                version,
                item.confidence,
                item.relevance,
                item.importance,
            )

        return (right, left) if quality(right) > quality(left) else (left, right)

    @staticmethod
    def _merge_refs(winner: ContextItem, loser: ContextItem) -> ContextItem:
        refs = tuple(dict.fromkeys((*winner.source_refs, *loser.source_refs)))
        return replace(winner, source_refs=refs)

    @staticmethod
    def _discard(item: ContextItem, reason: str) -> ContextSelectionDecision:
        return ContextSelectionDecision(
            item_id=item.item_id,
            source_kind=item.source_kind,
            selected=False,
            reason=reason,
            score=0.0,
            original_tokens=item.token_count,
            final_tokens=0,
            source_refs=item.source_refs,
        )


__all__ = ["ContextDeduplicator", "DeduplicationResult"]
