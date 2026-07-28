"""LLM 调用客户端。

当前通过 SiliconFlow OpenAI 兼容接口调用模型。这里保持很薄，只负责一次最小
聊天补全请求和流式输出，后续如果需要多轮消息，再扩展这个客户端。
"""

from collections.abc import Iterator

from openai import OpenAI

from app.core.config import settings


class LLMClient:
    """封装最小 LLM 调用能力。"""

    def __init__(
        self,
        model_name: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        llm_config = settings.llm
        self.model_name = model_name or llm_config.model_name
        self.client = OpenAI(
            api_key=api_key or llm_config.api_key,
            base_url=base_url or llm_config.base_url,
        )

    def _messages(self, user_text: str) -> list[dict[str, str]]:
        """构造最小对话消息。"""
        return [
            {"role": "system", "content": "你是一个简洁的中文数据分析助手。"},
            {"role": "user", "content": user_text},
        ]

    def chat(self, user_text: str) -> str:
        """发送单轮用户消息并返回模型文本回复。"""
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=self._messages(user_text),
        )
        return response.choices[0].message.content or ""

    def chat_stream(self, user_text: str) -> Iterator[str]:
        """流式发送单轮用户消息，逐段返回模型文本。"""
        stream = self.client.chat.completions.create(
            model=self.model_name,
            messages=self._messages(user_text),
            stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta
            content = getattr(delta, "content", None) or ""
            if content:
                yield content
