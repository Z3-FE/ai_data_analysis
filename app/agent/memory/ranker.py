"""记忆检索结果排序。"""

import math
from datetime import datetime, timezone

from app.agent.memory.interfaces import MemoryRecord, MemorySearchResult


class MemoryRanker:
    """结合相关性、重要性、可信度和时间衰减排序。"""

    def __init__(self, half_life_days: float = 30.0) -> None:
        # 时间衰减半衰期；重要长期事实仍会保留，只是排序分逐渐降低。
        self.half_life_days = max(0.1, half_life_days)

    def rank(
        self,
        candidates: list[tuple[MemoryRecord, float, str]],
        limit: int,
    ) -> list[MemorySearchResult]:
        """将候选转换成统一结果并返回前 limit 条。"""
        results = [
            MemorySearchResult(
                memory=memory,
                similarity=max(0.0, min(1.0, similarity)),
                score=self.score(memory, similarity),
                source=source,
            )
            for memory, similarity, source in candidates
        ]
        results.sort(key=lambda item: item.score, reverse=True)
        return results[: max(0, limit)]

    def score(self, memory: MemoryRecord, similarity: float) -> float:
        """计算一个可解释的综合排序分。"""
        now = datetime.now(timezone.utc)
        updated_at = memory.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - updated_at).total_seconds() / 86400)
        time_decay = math.pow(0.5, age_days / self.half_life_days)
        importance_weight = 0.8 + 0.2 * max(0.0, min(1.0, memory.importance))
        confidence_weight = 0.8 + 0.2 * max(0.0, min(1.0, memory.confidence))
        return (
            max(0.0, min(1.0, similarity))
            * time_decay
            * importance_weight
            * confidence_weight
        )
