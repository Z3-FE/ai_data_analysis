"""项目正式使用的 AutoLLM 客户端。

AutoLLM 建立在 LangChain 的 ChatOpenAI 之上，负责把不同 OpenAI 兼容服务
返回的正文、思考内容和元数据归一化。Agent 节点只依赖 LangChain Runnable
接口或 astream_auto，不直接接触具体服务商的客户端和字段。
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_openai import ChatOpenAI
from openai import APITimeoutError

from app.agent.utils.timeout_record import record_llm_timeout

Provider = Literal["mock", "openai-compatible"]
EventType = Literal["reasoning", "content", "metadata"]


@dataclass(frozen=True)
class AutoLLMConfig:
    """创建 AutoLLM 所需的模型和服务配置。"""

    provider: Provider
    model_name: str
    api_key: str
    base_url: str
    timeout_seconds: float
    max_tokens: int | None = None
    request_options: dict[str, Any] = field(default_factory=dict)
    include_usage: bool = True
    stream_only: bool = False


@dataclass(frozen=True)
class AutoLLMEvent:
    """AutoLLM 对外提供的流式事件。"""

    event_type: EventType
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """转换成可写入 SSE 的普通字典。"""
        return asdict(self)


@dataclass(frozen=True)
class AutoLLMResponse:
    """AutoLLM 的非流式响应。"""

    content: str
    reasoning: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def _read_value(source: Any, key: str) -> Any:
    """兼容字典、Pydantic 模型属性和扩展字段。"""
    if isinstance(source, dict):
        if key in source:
            return source[key]
        model_extra = source.get("model_extra")
        return model_extra.get(key) if isinstance(model_extra, dict) else None

    value = getattr(source, key, None)
    if value is not None:
        return value
    model_extra = getattr(source, "model_extra", None)
    return model_extra.get(key) if isinstance(model_extra, dict) else None


def _first_text_value(source: Any, keys: Sequence[str]) -> str:
    """从多个可能的服务商字段中读取第一个非空文本。"""
    for key in keys:
        value = _read_value(source, key)
        if isinstance(value, str) and value:
            return value
    return ""


def extract_reasoning(message: Any) -> str:
    """读取 reasoning_content、thinking 或标准 reasoning 内容块。"""
    additional_kwargs = getattr(message, "additional_kwargs", {}) or {}
    reasoning = _first_text_value(
        additional_kwargs,
        ("reasoning_content", "reasoning", "thinking"),
    )
    if reasoning:
        return reasoning

    for blocks in (
        getattr(message, "content_blocks", None),
        getattr(message, "content", None),
    ):
        if not isinstance(blocks, list):
            continue
        parts: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") not in {"reasoning", "thinking"}:
                continue
            text = _first_text_value(block, ("reasoning", "text", "content"))
            if text:
                parts.append(text)
        if parts:
            return "".join(parts)
    return ""


def extract_content(message: Any) -> str:
    """读取正文，并排除内容列表中的思考块。"""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return "" if content is None else str(content)

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"reasoning", "thinking"}:
            continue
        text = _first_text_value(block, ("text", "content"))
        if text:
            parts.append(text)
    return "".join(parts)


def extract_usage(message: Any) -> dict[str, Any]:
    """读取 LangChain 标准 Token 统计。"""
    usage = getattr(message, "usage_metadata", None)
    if isinstance(usage, dict):
        return usage
    if usage is not None and hasattr(usage, "model_dump"):
        return usage.model_dump(exclude_none=True)

    response_metadata = getattr(message, "response_metadata", {}) or {}
    token_usage = response_metadata.get("token_usage")
    return token_usage if isinstance(token_usage, dict) else {}


def extract_finish_reason(message: Any) -> str:
    """读取 LangChain 标准结束原因。"""
    response_metadata = getattr(message, "response_metadata", {}) or {}
    finish_reason = response_metadata.get("finish_reason")
    return finish_reason if isinstance(finish_reason, str) else ""


class ReasoningChatOpenAI(ChatOpenAI):
    """在 ChatOpenAI 默认转换后保留兼容接口的思考字段。"""

    @staticmethod
    def _raw_reasoning(source: Any) -> str:
        """读取兼容接口的原始 reasoning_content。"""
        return _first_text_value(
            source,
            ("reasoning_content", "reasoning", "thinking"),
        )

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict[str, Any],
        default_chunk_class: type,
        base_generation_info: dict[str, Any] | None,
    ) -> Any:
        """把流式 delta 中的思考字段补回 LangChain 消息。"""
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk,
            default_chunk_class,
            base_generation_info,
        )
        if generation_chunk is None:
            return None

        choices = chunk.get("choices", []) or chunk.get("chunk", {}).get(
            "choices", []
        )
        if choices:
            delta = choices[0].get("delta") or {}
            reasoning = self._raw_reasoning(delta)
            if reasoning:
                generation_chunk.message.additional_kwargs["reasoning_content"] = (
                    reasoning
                )
        return generation_chunk

    def _create_chat_result(
        self,
        response: Any,
        generation_info: dict[str, Any] | None = None,
    ) -> Any:
        """把非流式响应中的思考字段补回 LangChain 消息。"""
        result = super()._create_chat_result(response, generation_info)
        response_dict = (
            response
            if isinstance(response, dict)
            else response.model_dump(exclude_none=False, warnings=False)
        )
        for generation, choice in zip(
            result.generations,
            response_dict.get("choices", []),
        ):
            reasoning = self._raw_reasoning(choice.get("message", {}))
            if reasoning:
                generation.message.additional_kwargs["reasoning_content"] = reasoning
        return result


class MockLangChainModel:
    """不访问外部模型的 LangChain 兼容模型，用于本地回归。"""

    def invoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **_: Any,
    ) -> AIMessage:
        """返回带思考内容的模拟消息。"""
        del input, config
        return self._message()

    async def ainvoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **_: Any,
    ) -> AIMessage:
        """异步返回带思考内容的模拟消息。"""
        del input, config
        return self._message()

    def stream(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **_: Any,
    ) -> Iterator[AIMessageChunk]:
        """返回模拟的思考片段和正文片段。"""
        del input, config
        yield from self._chunks()

    async def astream(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **_: Any,
    ) -> AsyncIterator[AIMessageChunk]:
        """异步返回模拟的思考片段和正文片段。"""
        del input, config
        for chunk in self._chunks():
            yield chunk

    @staticmethod
    def _message() -> AIMessage:
        """构造模拟普通响应。"""
        return AIMessage(
            content="2017年销售额下降最明显的月份是12月。",
            additional_kwargs={
                "reasoning_content": "先比较各月份销售额，再计算环比下降幅度。"
            },
            response_metadata={"finish_reason": "stop"},
            usage_metadata={
                "input_tokens": 20,
                "output_tokens": 12,
                "total_tokens": 32,
            },
        )

    @staticmethod
    def _chunks() -> list[AIMessageChunk]:
        """构造模拟流式响应。"""
        return [
            AIMessageChunk(
                content="",
                additional_kwargs={"reasoning_content": "先确定时间范围。"},
            ),
            AIMessageChunk(
                content="",
                additional_kwargs={"reasoning_content": "再比较相邻月份的销售额。"},
            ),
            AIMessageChunk(content="2017年销售额下降最明显的月份是"),
            AIMessageChunk(
                content="12月。",
                response_metadata={"finish_reason": "stop"},
                usage_metadata={
                    "input_tokens": 20,
                    "output_tokens": 12,
                    "total_tokens": 32,
                },
            ),
        ]


class AutoLLMModelFactory:
    """根据 AutoLLM 配置创建底层 LangChain 模型。"""

    @staticmethod
    def create(config: AutoLLMConfig) -> ReasoningChatOpenAI:
        """创建 OpenAI 兼容的 Chat Model。"""
        if config.provider == "mock":
            return MockLangChainModel()  # type: ignore[return-value]
        if config.provider != "openai-compatible":
            raise ValueError(f"不支持的 LLM provider：{config.provider}")
        if not config.api_key:
            raise ValueError("未配置 LLM API Key。")
        if not config.base_url:
            raise ValueError("OpenAI 兼容模式必须配置 base_url。")

        model_kwargs: dict[str, Any] = {
            "model": config.model_name,
            "api_key": config.api_key,
            "base_url": config.base_url,
            "timeout": config.timeout_seconds,
            # Agent 自己负责空闲超时和错误反馈，避免 OpenAI 客户端达到
            # 配置等待阈值后悄悄重试，导致前端长时间停留在 running。
            "max_retries": 0,
            "temperature": 0,
            "stream_usage": config.include_usage,
        }
        if config.max_tokens is not None:
            model_kwargs["max_tokens"] = config.max_tokens
        if config.request_options:
            model_kwargs["extra_body"] = config.request_options
        return ReasoningChatOpenAI(**model_kwargs)


class AutoLLMAdapter(Runnable[Any, Any]):
    """项目正式使用的 LangChain Runnable 和自动流式事件适配器。"""

    def __init__(self, config: AutoLLMConfig, model: Any | None = None) -> None:
        """根据配置创建模型，测试可显式注入 LangChain 模型。"""
        self.adapter_config = config
        self.model = model or AutoLLMModelFactory.create(config)

    def invoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        """原样提供 LangChain 的同步 Runnable 调用。"""
        return self.model.invoke(input, config=config, **kwargs)

    async def ainvoke(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Any:
        """原样提供 LangChain 的异步 Runnable 调用。"""
        try:
            return await asyncio.wait_for(
                self.model.ainvoke(input, config=config, **kwargs),
                timeout=self.adapter_config.timeout_seconds,
            )
        except (asyncio.TimeoutError, APITimeoutError) as exc:
            error = (
                "LLM 非流式响应超时（连续 "
                f"{self.adapter_config.timeout_seconds:g} 秒没有返回）。"
            )
            record_llm_timeout(
                node="auto_llm",
                call_mode="invoke",
                timeout_kind="non_stream_idle",
                timeout_seconds=self.adapter_config.timeout_seconds,
                error=error,
                llm_client=self,
            )
            raise TimeoutError(
                error
            ) from exc

    def stream(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> Iterator[Any]:
        """原样提供 LangChain 的同步流式调用。"""
        yield from self.model.stream(input, config=config, **kwargs)

    async def astream(
        self,
        input: Any,
        config: RunnableConfig | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """原样提供 LangChain 的异步流式调用。"""
        try:
            async for chunk in self.model.astream(input, config=config, **kwargs):
                yield chunk
        except APITimeoutError as exc:
            error = (
                "LLM 流式请求超时（连续 "
                f"{self.adapter_config.timeout_seconds:g} 秒没有返回）。"
            )
            record_llm_timeout(
                node="auto_llm",
                call_mode="stream",
                timeout_kind="stream_idle",
                timeout_seconds=self.adapter_config.timeout_seconds,
                error=error,
                llm_client=self,
            )
            raise TimeoutError(error) from exc

    async def ainvoke_auto(self, input: Any) -> AutoLLMResponse:
        """执行调用并返回正文、思考和元数据。"""
        if self.adapter_config.stream_only:
            return await self._collect_auto_stream(input)
        message = await self.ainvoke(input)
        return AutoLLMResponse(
            content=extract_content(message),
            reasoning=extract_reasoning(message),
            metadata=self._metadata(message),
        )

    async def astream_auto(self, input: Any) -> AsyncIterator[AutoLLMEvent]:
        """把底层消息流转换成 reasoning、content、metadata 三类事件。"""
        usage: dict[str, Any] = {}
        finish_reason = ""
        reasoning_received = False
        async for chunk in self.astream(input):
            reasoning = extract_reasoning(chunk)
            if reasoning:
                reasoning_received = True
                yield AutoLLMEvent(event_type="reasoning", text=reasoning)
            content = extract_content(chunk)
            if content:
                yield AutoLLMEvent(event_type="content", text=content)
            chunk_usage = extract_usage(chunk)
            if chunk_usage:
                usage = chunk_usage
            chunk_finish_reason = extract_finish_reason(chunk)
            if chunk_finish_reason:
                finish_reason = chunk_finish_reason
        yield AutoLLMEvent(
            event_type="metadata",
            metadata={
                "provider": self.adapter_config.provider,
                "model_name": self.adapter_config.model_name,
                "finish_reason": finish_reason,
                "usage": usage,
                "reasoning_received": reasoning_received,
            },
        )

    async def _collect_auto_stream(self, input: Any) -> AutoLLMResponse:
        """为只支持流式的模型累积完整响应。"""
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        metadata: dict[str, Any] = {}
        async for event in self.astream_auto(input):
            if event.event_type == "content":
                content_parts.append(event.text)
            elif event.event_type == "reasoning":
                reasoning_parts.append(event.text)
            else:
                metadata = event.metadata
        return AutoLLMResponse(
            content="".join(content_parts),
            reasoning="".join(reasoning_parts),
            metadata=metadata,
        )

    def _metadata(self, message: Any) -> dict[str, Any]:
        """生成稳定的响应元数据。"""
        return {
            "provider": self.adapter_config.provider,
            "model_name": self.adapter_config.model_name,
            "finish_reason": extract_finish_reason(message),
            "usage": extract_usage(message),
            "reasoning_received": bool(extract_reasoning(message)),
        }

    async def aclose(self) -> None:
        """关闭底层 OpenAI 异步客户端。"""
        client = getattr(self.model, "root_async_client", None)
        close = getattr(client, "close", None)
        if close is None:
            return
        result = close()
        if inspect.isawaitable(result):
            await result
