"""ContextEngine 使用的精确 token 计数与截断。"""

import json
from collections.abc import Sequence

import tiktoken

from app.agent.context_engine.interfaces import TokenCounter


class TiktokenCounter(TokenCounter):
    """使用项目已安装的 tiktoken 计算真实 token 数。"""

    def __init__(
        self,
        *,
        model_name: str | None = None,
        fallback_encoding: str = "o200k_base",
    ) -> None:
        # 已知模型使用官方映射，私有或兼容模型使用指定通用编码。
        try:
            self.encoding = (
                tiktoken.encoding_for_model(model_name)
                if model_name
                else tiktoken.get_encoding(fallback_encoding)
            )
        except KeyError:
            self.encoding = tiktoken.get_encoding(fallback_encoding)

    def count_text(self, text: str) -> int:
        """返回文本的实际编码 token 数。"""
        # 按初始化时选定的编码器转成 token ID 序列，长度即 token 数，而非字符数。
        return len(self.encoding.encode(text or ""))

    def count_messages(self, messages: Sequence[dict]) -> int:
        """计算 role/content 消息，并计入聊天协议的保守结构开销。"""
        # ① 预留整组消息的固定开销；这里与下面的每条 4 token 都是协议估算值。
        total = 2
        for message in messages:
            # ② 每条消息先计入结构开销和角色文本，不能只统计正文。
            total += 4
            total += self.count_text(str(message.get("role") or ""))
            content = message.get("content", "")
            # ③ 字符串正文直接编码；结构化正文先序列化，按序列化文本计数。
            if isinstance(content, str):
                total += self.count_text(content)
            else:
                total += self.count_text(
                    json.dumps(content, ensure_ascii=False, default=str)
                )
        # ④ 返回本地预算计数，不保证与模型服务端最终报告的 token 用量完全一致。
        return total

    def truncate(self, text: str, max_tokens: int, *, marker: str = "") -> str:
        """按 token 边界截断文本，并保证附加标记也处于预算内。"""
        if max_tokens <= 0:
            return ""
        tokens = self.encoding.encode(text or "")
        if len(tokens) <= max_tokens:
            return text
        marker_tokens = self.encoding.encode(marker) if marker else []
        if len(marker_tokens) >= max_tokens:
            return self.encoding.decode(marker_tokens[:max_tokens])
        keep = max_tokens - len(marker_tokens)
        return self.encoding.decode(tokens[:keep]) + marker

    def split_text(self, text: str, max_tokens: int) -> list[str]:
        """按 token 边界切分文本，供长历史分批摘要。"""
        if max_tokens <= 0:
            raise ValueError("max_tokens 必须大于 0")
        tokens = self.encoding.encode(text or "")
        return [
            self.encoding.decode(tokens[start : start + max_tokens])
            for start in range(0, len(tokens), max_tokens)
        ]


__all__ = ["TiktokenCounter"]
