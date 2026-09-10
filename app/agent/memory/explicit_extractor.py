"""显式记忆请求的确定性兜底提取器。

生产流程优先使用 LLM 把任意显式事实结构化。本模块只保证 LLM 不可用时
用户明确要求保存的原文仍不会丢失，不在代码中穷举姓名、语言或业务字段。
"""

import re

from app.agent.memory.contracts import MemoryCandidate, TurnMemoryInput
from app.agent.memory.enums import MemoryType

_MEMORY_REQUEST_PATTERN = re.compile(
    r"(?:请|帮我|麻烦)?(?:记住|记一下|记录一下|保存到记忆(?:库)?|加入记忆)"
    r"|\b(?:remember|save (?:this|that) (?:to|in) memory)\b",
    re.I,
)
_ATTACHMENT_ONLY_PATTERN = re.compile(
    r"^(?:这个|该|当前|刚才(?:的)?|上面(?:的)?|我上传(?:的)?)?"
    r"(?:附件|文件|图片|截图|音频|视频)"
    r"(?:(?:中|里|中的|里的)(?:内容|信息|数据)?|(?:内容|信息|数据))?$"
)
_ATTACHMENT_REFERENCE_PATTERN = re.compile(
    r"(?:这个|这些|该|当前|刚才(?:的)?|上面(?:的)?|我上传(?:的)?)?"
    r"(?:附件|文件|图片|截图|音频|视频)"
)


class ExplicitMemoryExtractor:
    """把无法结构化的显式记忆请求保真保存为 Semantic 候选。"""

    def __init__(self, *, memory_request_pattern: re.Pattern[str] | None = None) -> None:
        # 与 Eligibility 共享产品级显式记忆意图，避免“已判定显式”却无法兜底提取。
        self.memory_request_pattern = memory_request_pattern or _MEMORY_REQUEST_PATTERN

    def extract(self, turn: TurnMemoryInput) -> list[MemoryCandidate]:
        """从本轮用户输入提取零到多条显式候选。"""
        text = turn.input_text.strip()
        if not text:
            return []
        if not self._contains_memory_request(text):
            return []
        content = self._generic_content(text)
        if not content or _ATTACHMENT_ONLY_PATTERN.fullmatch(content):
            return []
        return [
            MemoryCandidate(
                memory_type=MemoryType.SEMANTIC,
                content=content,
                # 兜底不猜字段含义；后续同文可去重，人工或 LLM 可再结构化。
                fact_key="",
                value=content,
                importance=0.7,
                confidence=1.0,
                reason="用户明确要求保存该信息，LLM 结构化不可用。",
            )
        ]

    def _contains_memory_request(self, text: str) -> bool:
        return bool(self.memory_request_pattern.search(text))

    def references_attachment(self, text: str) -> bool:
        """判断显式记忆请求是否确实指向本轮附件。"""
        return bool(
            self.memory_request_pattern.search(text)
            and _ATTACHMENT_REFERENCE_PATTERN.search(text)
        )

    def _generic_content(self, text: str) -> str:
        """去掉配置的请求前缀，避免把触发词本身写成记忆。"""
        content = self.memory_request_pattern.sub("", text, count=1)
        return content.strip().lstrip(":： \t").rstrip("。.!！")[:4000]

__all__ = ["ExplicitMemoryExtractor"]
