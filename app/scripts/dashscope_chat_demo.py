"""验证阿里云百炼 LangChain Chat Model 的最小 Demo。

运行方式：
  DASHSCOPE_API_KEY=sk-xxx uv run python -m app.scripts.dashscope_chat_demo

也可以把 DASHSCOPE_MODEL 和 DASHSCOPE_BASE_URL 放进项目根目录的 env 文件。
脚本通过 LangChain 的 ChatOpenAI 调用 OpenAI 兼容接口，不参与 Agent 图执行，
便于先确认模型、鉴权、普通调用、流式调用和 reasoning_content 是否符合预期。
"""

import argparse
import asyncio
import os
import sys
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.core.config import settings


def _parse_args() -> argparse.Namespace:
    """解析 Demo 的模型、问题和思考模式参数。"""
    parser = argparse.ArgumentParser(description="验证阿里云百炼 Chat API")
    parser.add_argument(
        "--query",
        default="请用一句话说明数据分析报告中的表格组件有什么作用。",
        help="发送给模型的问题",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("DASHSCOPE_MODEL", settings.llm.model_name),
        help="模型名称，默认读取 DASHSCOPE_MODEL 或项目配置",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("DASHSCOPE_BASE_URL", settings.llm.base_url),
        help="OpenAI 兼容接口地址",
    )
    parser.add_argument(
        "--request-options-json",
        default=os.getenv("LLM_REQUEST_OPTIONS_JSON"),
        help="模型扩展参数 JSON；不传则采用服务商默认值",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="只测试普通响应，不测试流式响应",
    )
    return parser.parse_args()


def _api_key() -> str:
    """只从专用环境变量读取百炼 Key，避免误用旧服务的 Key。"""
    return os.getenv("DASHSCOPE_API_KEY", settings.llm.api_key)


def _content_text(content: object) -> str:
    """把 LangChain 消息 content 统一为可打印文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return "" if content is None else str(content)


def _reasoning_text(message: Any) -> str:
    """兼容不同 LangChain/OpenAI 版本保存思考字段的位置。"""
    additional_kwargs = getattr(message, "additional_kwargs", {}) or {}
    reasoning = additional_kwargs.get("reasoning_content")
    if isinstance(reasoning, str):
        return reasoning

    response_metadata = getattr(message, "response_metadata", {}) or {}
    reasoning = response_metadata.get("reasoning_content")
    if isinstance(reasoning, str):
        return reasoning

    content = getattr(message, "content", None)
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") in {
                "reasoning",
                "thinking",
            }:
                text = block.get("text") or block.get("content")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


def _build_model(args: argparse.Namespace) -> ChatOpenAI:
    """按项目当前配置创建 LangChain ChatOpenAI。"""
    extra_body = None
    if args.request_options_json:
        import json

        extra_body = json.loads(args.request_options_json)
    return ChatOpenAI(
        model=args.model,
        api_key=_api_key(),
        base_url=args.base_url,
        timeout=settings.llm.timeout_seconds,
        temperature=0,
        max_tokens=settings.llm.max_tokens,
        extra_body=extra_body,
    )


async def _run(args: argparse.Namespace) -> int:
    """通过 LangChain 执行普通或流式调用，并打印关键响应字段。"""
    if not _api_key():
        print(
            "未配置 DASHSCOPE_API_KEY，请先设置阿里云百炼 API Key。",
            file=sys.stderr,
        )
        return 1

    model = _build_model(args)
    messages = [
        SystemMessage(content="你是一个简洁的数据分析助手。"),
        HumanMessage(content=args.query),
    ]

    if args.no_stream:
        message = await model.ainvoke(messages)
        print("=== LangChain 普通响应 ===")
        print(f"content: {_content_text(message.content)}")
        print(f"reasoning_content: {_reasoning_text(message)}")
        print(f"response_metadata: {message.response_metadata}")
        print(
            "reasoning_content 未暴露时，仍可从 response_metadata.token_usage "
            "查看 reasoning_tokens。"
        )
        return 0

    reasoning_parts: list[str] = []
    content_parts: list[str] = []
    print("=== LangChain reasoning_content（思考过程）===")
    async for chunk in model.astream(messages):
        reasoning = _reasoning_text(chunk)
        content = _content_text(chunk.content)
        if reasoning:
            reasoning_parts.append(reasoning)
            print(reasoning, end="", flush=True)
        if content:
            if not content_parts:
                print("\n\n=== content（最终结果）===")
            content_parts.append(content)
            print(content, end="", flush=True)
    print("\n\n=== 汇总 ===")
    print(f"reasoning_content 字符数：{len(''.join(reasoning_parts))}")
    print(f"content 字符数：{len(''.join(content_parts))}")
    if not reasoning_parts:
        print("本次 LangChain 流式消息未暴露 reasoning_content 文本。")
    return 0


def main() -> None:
    """脚本入口。"""
    raise SystemExit(asyncio.run(_run(_parse_args())))


if __name__ == "__main__":
    main()
