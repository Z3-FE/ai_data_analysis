"""跨来源候选评分、预算选择和本轮压缩。"""

from dataclasses import dataclass, replace

from app.agent.context_engine.contracts import (
    ContextItem,
    ContextPolicy,
    ContextSelectionDecision,
    ContextSourceKind,
)
from app.agent.context_engine.interfaces import ContextCompressor, TokenCounter


@dataclass(frozen=True, slots=True)
class ContextSelectionResult:
    """Selector 的选中候选和完整处理决定。"""

    items: tuple[ContextItem, ...]
    decisions: tuple[ContextSelectionDecision, ...]
    used_tokens: int


class ContextSelector:
    """在统一分数与分区预算下选择候选，不改写原始记忆。"""

    _history_kinds = {ContextSourceKind.WORKING, ContextSourceKind.SUMMARY}

    def __init__(
        self,
        *,
        policy: ContextPolicy,
        token_counter: TokenCounter,
        compressor: ContextCompressor,
    ) -> None:
        self.policy = policy
        self.token_counter = token_counter
        self.compressor = compressor

    async def select(
        self, items: list[ContextItem], *, token_budget: int
    ) -> ContextSelectionResult:
        """先保留明确引用，再分别竞争 Working 和其他上下文预算。"""
        if token_budget <= 0:
            return ContextSelectionResult(
                (),
                tuple(
                    self._rejected(item, "没有可用候选 token 预算") for item in items
                ),
                0,
            )
        scored = [(item, self._score(item)) for item in items]
        required = sorted(
            ((item, score) for item, score in scored if item.required),
            key=lambda pair: pair[1],
            reverse=True,
        )
        optional = [(item, score) for item, score in scored if not item.required]
        selected: list[ContextItem] = []
        decisions: list[ContextSelectionDecision] = []
        remaining = token_budget

        for position, (item, score) in enumerate(required):
            required_left = len(required) - position
            fair_share = remaining // required_left
            chosen, decision = await self._fit(
                item,
                score=score,
                remaining=min(remaining, fair_share),
                required=True,
            )
            decisions.append(decision)
            if chosen is None:
                raise ValueError(
                    f"明确要求保留的上下文 {item.item_id} 无法放入本轮 token 预算"
                )
            selected.append(chosen)
            remaining -= chosen.token_count

        history_budget = min(
            remaining, int(token_budget * self.policy.working_history_ratio)
        )
        summary_items = sorted(
            (
                pair
                for pair in optional
                if pair[0].source_kind is ContextSourceKind.SUMMARY
            ),
            key=lambda pair: pair[1],
            reverse=True,
        )
        working_items = sorted(
            (
                pair
                for pair in optional
                if pair[0].source_kind is ContextSourceKind.WORKING
            ),
            key=lambda pair: (
                self._message_index(pair[0]),
                pair[1],
            ),
            reverse=True,
        )
        history_used = 0
        summary_budget = int(history_budget * (1.0 - self.policy.recent_history_ratio))
        for item, score in summary_items:
            available = min(remaining, max(0, summary_budget - history_used))
            chosen, decision = await self._fit(
                item, score=score, remaining=available, required=False
            )
            decisions.append(decision)
            if chosen is not None:
                selected.append(chosen)
                history_used += chosen.token_count
                remaining -= chosen.token_count
        for item, score in working_items:
            available = min(remaining, max(0, history_budget - history_used))
            chosen, decision = await self._fit(
                item, score=score, remaining=available, required=False
            )
            decisions.append(decision)
            if chosen is not None:
                selected.append(chosen)
                history_used += chosen.token_count
                remaining -= chosen.token_count

        others = sorted(
            (
                pair
                for pair in optional
                if pair[0].source_kind not in self._history_kinds
            ),
            key=lambda pair: pair[1],
            reverse=True,
        )
        for item, score in others:
            chosen, decision = await self._fit(
                item, score=score, remaining=remaining, required=False
            )
            decisions.append(decision)
            if chosen is not None:
                selected.append(chosen)
                remaining -= chosen.token_count

        selected.sort(key=self._output_order)
        return ContextSelectionResult(
            items=tuple(selected),
            decisions=tuple(decisions),
            used_tokens=token_budget - remaining,
        )

    async def _fit(
        self,
        item: ContextItem,
        *,
        score: float,
        remaining: int,
        required: bool,
    ) -> tuple[ContextItem | None, ContextSelectionDecision]:
        original_tokens = item.token_count or self.token_counter.count_text(
            item.content
        )
        if not required and score < self.policy.min_selection_score:
            return None, self._rejected(item, "综合分低于选择阈值", score)
        max_tokens = min(remaining, self.policy.max_item_tokens)
        if original_tokens <= max_tokens:
            chosen = replace(item, token_count=original_tokens)
            return chosen, self._accepted(
                chosen, score, original_tokens, compressed=False
            )
        if max_tokens < self.policy.min_compression_tokens:
            return None, self._rejected(item, "剩余预算不足以形成有意义的片段", score)
        content = await self.compressor.compress(item, max_tokens=max_tokens)
        final_tokens = self.token_counter.count_text(content)
        if not content.strip() or final_tokens > max_tokens:
            return None, self._rejected(item, "候选压缩后仍超出预算", score)
        chosen = replace(item, content=content, token_count=final_tokens)
        return chosen, self._accepted(chosen, score, original_tokens, compressed=True)

    def _score(self, item: ContextItem) -> float:
        policy = self.policy
        priority = max(0.0, min(1.0, item.priority / 100))
        total_weight = (
            policy.relevance_weight
            + policy.importance_weight
            + policy.confidence_weight
            + policy.recency_weight
            + policy.priority_weight
        )
        raw = (
            self._unit(item.relevance) * policy.relevance_weight
            + self._unit(item.importance) * policy.importance_weight
            + self._unit(item.confidence) * policy.confidence_weight
            + self._unit(item.recency) * policy.recency_weight
            + priority * policy.priority_weight
        )
        return raw / total_weight

    @staticmethod
    def _unit(value: float) -> float:
        return max(0.0, min(1.0, float(value)))

    @staticmethod
    def _message_index(item: ContextItem) -> int:
        try:
            return int(item.structured_data.get("message_index", -1))
        except (TypeError, ValueError):
            return -1

    @classmethod
    def _output_order(cls, item: ContextItem) -> tuple[int, int, str]:
        order = {
            ContextSourceKind.SUMMARY: 0,
            ContextSourceKind.SEMANTIC: 1,
            ContextSourceKind.EPISODIC: 2,
            ContextSourceKind.PERCEPTUAL: 3,
            ContextSourceKind.RAG: 4,
            ContextSourceKind.WORKING: 5,
        }
        return order[item.source_kind], cls._message_index(item), item.item_id

    @staticmethod
    def _accepted(
        item: ContextItem, score: float, original_tokens: int, *, compressed: bool
    ) -> ContextSelectionDecision:
        return ContextSelectionDecision(
            item_id=item.item_id,
            source_kind=item.source_kind,
            selected=True,
            reason="明确引用，优先保留" if item.required else "综合分和预算允许",
            score=round(score, 6),
            original_tokens=original_tokens,
            final_tokens=item.token_count,
            compressed=compressed,
            source_refs=item.source_refs,
        )

    @staticmethod
    def _rejected(
        item: ContextItem, reason: str, score: float = 0.0
    ) -> ContextSelectionDecision:
        return ContextSelectionDecision(
            item_id=item.item_id,
            source_kind=item.source_kind,
            selected=False,
            reason=reason,
            score=round(score, 6),
            original_tokens=item.token_count,
            final_tokens=0,
            source_refs=item.source_refs,
        )


__all__ = ["ContextSelectionResult", "ContextSelector"]
