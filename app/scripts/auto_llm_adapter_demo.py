"""AutoLLM 正式客户端 Demo。

这个脚本只负责构造命令行配置和打印结果；适配逻辑全部来自正式客户端
app.clients.auto_llm_client，避免 Demo 与 Agent 主链路出现两套实现。

本地 Mock 测试：

    uv run python -m app.scripts.auto_llm_adapter_demo \
      --provider mock --call-mode both

OpenAI 兼容模型测试：

    uv run python -m app.scripts.auto_llm_adapter_demo \
      --provider openai-compatible \
      --model qwen3.8-max \
      --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
      --api-key-env DASHSCOPE_API_KEY \
      --call-mode stream
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict
from typing import Any

from langchain_core.messages import HumanMessage
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

from app.clients.auto_llm_client import AutoLLMAdapter, AutoLLMConfig
from app.core.config import settings


def _parse_request_options(raw_value: str | None) -> dict[str, Any]:
    """解析模型扩展参数；未提供时使用服务商默认值。"""
    if not raw_value:
        return {}
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise ValueError("request-options-json 必须是合法 JSON。") from exc
    if not isinstance(value, dict):
        raise ValueError("request-options-json 必须是 JSON 对象。")
    return value


def _build_config(args: argparse.Namespace) -> AutoLLMConfig:
    """把命令行参数转换成正式 AutoLLM 配置。"""
    api_key = os.getenv(args.api_key_env, "") if args.provider != "mock" else "mock"
    if args.provider != "mock" and not api_key:
        raise ValueError(f"未配置环境变量 {args.api_key_env}。")
    return AutoLLMConfig(
        provider=args.provider,
        model_name=args.model,
        api_key=api_key,
        base_url=args.base_url,
        timeout_seconds=settings.llm.timeout_seconds,
        max_tokens=None,
        request_options=_parse_request_options(args.request_options_json),
        include_usage=not args.no_usage,
        stream_only=args.stream_only,
    )


async def _ainvoke_with_progress(
    adapter: AutoLLMAdapter,
    messages: list[HumanMessage],
    timeout_seconds: float,
):
    """执行非流式调用并周期性打印等待状态。"""
    task = asyncio.create_task(adapter.ainvoke_auto(messages))
    started_at = time.monotonic()
    while True:
        elapsed = time.monotonic() - started_at
        remaining = timeout_seconds - elapsed
        if remaining <= 0:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise TimeoutError(
                f"普通调用超过 {timeout_seconds:.0f} 秒仍未完成；"
                "深度思考模型建议改用 --call-mode stream。"
            )
        try:
            return await asyncio.wait_for(
                asyncio.shield(task),
                timeout=min(10.0, remaining),
            )
        except asyncio.TimeoutError:
            print(
                f"仍在等待模型完成思考和回答，已等待 {time.monotonic() - started_at:.0f} 秒...",
                flush=True,
            )


async def _run(args: argparse.Namespace) -> int:
    """运行 Runnable、普通响应和流式响应三类检查。"""
    adapter = AutoLLMAdapter(_build_config(args))
    messages = [HumanMessage(content=args.query)]
    try:
        if args.provider == "mock":
            prompt = ChatPromptTemplate.from_template("请分析：{query}")
            chain = prompt | adapter | StrOutputParser()
            print("=== LangChain Runnable 链 ===")
            print(await chain.ainvoke({"query": args.query}))

        if args.call_mode in {"invoke", "both"}:
            print("\n=== AutoLLM 普通响应 ===")
            response = await _ainvoke_with_progress(
                adapter,
                messages,
                timeout_seconds=args.invoke_timeout,
            )
            print(json.dumps(asdict(response), ensure_ascii=False, indent=2, default=str))

        if args.call_mode in {"stream", "both"}:
            print("\n=== AutoLLM 流式事件 ===")
            async for event in adapter.astream_auto(messages):
                print(json.dumps(event.to_dict(), ensure_ascii=False, default=str))
    finally:
        await adapter.aclose()
    return 0


def _parse_args() -> argparse.Namespace:
    """解析 Demo 参数。"""
    parser = argparse.ArgumentParser(description="AutoLLM 正式客户端 Demo")
    parser.add_argument(
        "--provider",
        choices=("mock", "openai-compatible"),
        default=os.getenv("LLM_PROVIDER", "mock"),
    )
    parser.add_argument(
        "--model",
        default=os.getenv("LLM_MODEL", settings.llm.model_name),
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("LLM_BASE_URL", settings.llm.base_url),
    )
    parser.add_argument(
        "--api-key-env",
        default=os.getenv("LLM_API_KEY_ENV", "DASHSCOPE_API_KEY"),
        help="保存 API Key 的环境变量名称，不是 API Key 本身",
    )
    parser.add_argument(
        "--request-options-json",
        default=os.getenv("LLM_REQUEST_OPTIONS_JSON"),
        help="模型扩展参数 JSON；不传则采用服务商默认值",
    )
    parser.add_argument(
        "--call-mode",
        choices=("invoke", "stream", "both"),
        default="both",
    )
    parser.add_argument(
        "--invoke-timeout",
        type=float,
        default=settings.llm.timeout_seconds,
    )
    parser.add_argument("--stream-only", action="store_true")
    parser.add_argument("--no-usage", action="store_true")
    parser.add_argument(
        "--query",
        default="分析2017年各月销售额变化，并说明下降最明显的月份。",
    )
    return parser.parse_args()


def main() -> None:
    """脚本入口。"""
    try:
        raise SystemExit(asyncio.run(_run(_parse_args())))
    except (RuntimeError, TimeoutError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
