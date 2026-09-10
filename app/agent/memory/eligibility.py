"""本轮对话是否值得运行长期记忆提取的确定性预筛选。"""

import re
from dataclasses import dataclass
from re import Pattern

from app.agent.memory.contracts import FormationEligibility, TurnMemoryInput
from app.agent.memory.enums import MemoryFormationTrigger

_NEGATIVE_MEMORY_PATTERN = re.compile(
    r"(?:不要|别|无需|不需要|禁止).{0,8}(?:记住|记录|保存(?:到)?记忆)"
    r"|\b(?:do not|don't|never) (?:remember|save (?:this|that)(?: to| in)? memory)\b",
    re.I,
)
_EXPLICIT_MEMORY_PATTERN = re.compile(
    r"(?:请|帮我|麻烦)?(?:记住|记一下|记录一下|保存到记忆(?:库)?|加入记忆)"
    r"|\b(?:remember|save (?:this|that) (?:to|in) memory)\b",
    re.I,
)


@dataclass(frozen=True, slots=True)
class MemoryEligibilityPolicy:
    """长期记忆提取预筛选策略；领域项目可以替换，而无需改判断代码。"""

    # 只有已经给用户形成有效结果的轮次才允许提取长期记忆。
    allowed_statuses: frozenset[str] = frozenset({"completed", "partial"})
    # 自动形成必须同时存在用户输入和助手可见输出。
    require_assistant_content: bool = True
    # 识别“不要保存”意图的规则；可以按产品语言或领域替换。
    negative_intent_pattern: Pattern[str] = _NEGATIVE_MEMORY_PATTERN
    # 识别“请记住”意图的规则；必须和显式兜底提取器使用同一规则。
    explicit_intent_pattern: Pattern[str] = _EXPLICIT_MEMORY_PATTERN


class MemoryEligibilityEvaluator:
    """用低成本规则挡住寒暄、失败轮次和无长期价值的普通问答。"""

    def __init__(
        self,
        *,
        automatic_formation_enabled: bool = True,
        policy: MemoryEligibilityPolicy | None = None,
    ) -> None:
        # 可以关闭自动形成，但用户明确“记住”的请求始终保留。
        self.automatic_formation_enabled = automatic_formation_enabled
        # 这里只判断是否值得运行提取器，不在规则中枚举所有可记忆事实。
        self.policy = policy or MemoryEligibilityPolicy()

    def evaluate(self, turn: TurnMemoryInput) -> FormationEligibility:
        """返回本轮是否进入提取流程及可审计原因。"""
        text = turn.input_text.strip()
        if turn.status not in self.policy.allowed_statuses:
            return self._skip("失败或未完成轮次不形成长期记忆。")
        if not text:
            return self._skip("用户输入为空。")
        if self.policy.negative_intent_pattern.search(text):
            return self._skip("用户明确要求不要保存该内容。")
        if self.policy.explicit_intent_pattern.search(text):
            return FormationEligibility(
                eligible=True,
                trigger=MemoryFormationTrigger.EXPLICIT,
                reason="用户明确要求保存长期记忆。",
            )
        if not self.automatic_formation_enabled:
            return self._skip("自动记忆形成当前已关闭。")
        if self.policy.require_assistant_content and not turn.assistant_content.strip():
            return self._skip("本轮没有助手可见输出。")
        return FormationEligibility(
            eligible=True,
            trigger=MemoryFormationTrigger.AUTOMATIC,
            reason=(
                "成功轮次进入受控 LLM 提取；是否产生长期记忆由提取器返回空或候选决定。"
            ),
        )

    @staticmethod
    def _skip(reason: str) -> FormationEligibility:
        return FormationEligibility(
            eligible=False,
            trigger=MemoryFormationTrigger.SKIPPED,
            reason=reason,
        )


__all__ = ["MemoryEligibilityEvaluator", "MemoryEligibilityPolicy"]
