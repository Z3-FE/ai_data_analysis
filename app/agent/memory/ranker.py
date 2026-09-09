"""按记忆类型融合召回信号并排序。"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.agent.memory.enums import MemoryType
from app.agent.memory.interfaces import MemoryRecord, MemorySearchResult


@dataclass(slots=True)
class MemoryRecallCandidate:
    """同一条记忆从多个检索后端得到的召回信号。"""

    # PostgreSQL 中复核过权限和生命周期的事实记录。
    memory: MemoryRecord
    # 每个召回来源的归一化分数；同一来源重复命中时保留最高分。
    signals: dict[str, float] = field(default_factory=dict)

    def add_signal(self, source: str, score: float) -> None:
        """合并一个召回信号。"""
        normalized = max(0.0, min(1.0, float(score)))
        self.signals[source] = max(self.signals.get(source, 0.0), normalized)


@dataclass(frozen=True, slots=True)
class MemoryRankingPolicy:
    """一种长期记忆的召回评分权重。"""

    # 词法、向量和图关系融合后的相关性权重。
    relevance_weight: float
    # 显式附件 ID 命中的权重；非感知记忆为 0。
    reference_weight: float
    # 时间近因性权重；稳定语义事实为 0。
    recency_weight: float
    # 形成阶段给出的长期价值权重。
    importance_weight: float
    # 提取器或人工确认的可信度权重。
    confidence_weight: float
    # 时间衰减半衰期；不使用近因性时为空。
    half_life_days: float | None = None


DEFAULT_RANKING_POLICIES = {
    MemoryType.SEMANTIC: MemoryRankingPolicy(0.65, 0.0, 0.0, 0.2, 0.15),
    MemoryType.EPISODIC: MemoryRankingPolicy(0.55, 0.0, 0.2, 0.15, 0.1, 45.0),
    MemoryType.PERCEPTUAL: MemoryRankingPolicy(
        0.45, 0.25, 0.15, 0.1, 0.05, 180.0
    ),
}


class MemoryRanker:
    """对一种长期记忆使用专属的多因素评分策略。"""

    def __init__(
        self,
        memory_type: MemoryType,
        policy: MemoryRankingPolicy | None = None,
    ) -> None:
        if memory_type is MemoryType.WORKING:
            raise ValueError("Working Memory 使用线程内词法排序，不使用长期记忆排序器")
        # 评分策略由记忆语义决定，不能让稳定事实和历史事件共用同一半衰期。
        self.memory_type = memory_type
        self.policy = policy or DEFAULT_RANKING_POLICIES[memory_type]

    def rank(
        self,
        candidates: list[MemoryRecallCandidate],
        limit: int,
    ) -> list[MemorySearchResult]:
        """将候选转换成统一结果并返回前 limit 条。"""
        results = [
            MemorySearchResult(
                memory=candidate.memory,
                similarity=self._relevance(candidate.signals),
                score=self.score(candidate),
                source=",".join(sorted(candidate.signals)),
                signals=dict(candidate.signals),
            )
            for candidate in candidates
            if candidate.signals
        ]
        results.sort(key=lambda item: item.score, reverse=True)
        return results[: max(0, limit)]

    def score(self, candidate: MemoryRecallCandidate) -> float:
        """根据记忆类型计算可解释的综合排序分。"""
        memory = candidate.memory
        relevance = self._relevance(candidate.signals)
        importance = max(0.0, min(1.0, memory.importance))
        confidence = max(0.0, min(1.0, memory.confidence))
        policy = self.policy
        reference = candidate.signals.get("reference", 0.0)
        recency = (
            self._time_decay(memory, half_life_days=policy.half_life_days)
            if policy.half_life_days is not None
            else 0.0
        )
        return (
            relevance * policy.relevance_weight
            + reference * policy.reference_weight
            + recency * policy.recency_weight
            + importance * policy.importance_weight
            + confidence * policy.confidence_weight
        )

    def _relevance(self, signals: dict[str, float]) -> float:
        """融合词法、向量和图关系分数；附件引用单独参与最终评分。"""
        weighted = {
            "lexical": 0.9,
            "vector": 1.0,
            "graph": 1.0,
        }
        misses = [
            1.0 - max(0.0, min(1.0, signals.get(name, 0.0))) * weight
            for name, weight in weighted.items()
        ]
        return 1.0 - math.prod(misses)

    @staticmethod
    def _time_decay(memory: MemoryRecord, *, half_life_days: float) -> float:
        """根据更新时间计算指定半衰期的近因性。"""
        now = datetime.now(timezone.utc)
        updated_at = memory.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - updated_at).total_seconds() / 86400)
        return math.pow(0.5, age_days / max(0.1, half_life_days))


__all__ = [
    "DEFAULT_RANKING_POLICIES",
    "MemoryRanker",
    "MemoryRankingPolicy",
    "MemoryRecallCandidate",
]
