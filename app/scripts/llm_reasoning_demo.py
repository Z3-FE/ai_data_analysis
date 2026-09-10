"""验证 SiliconFlow LLM 的思考过程流式返回。

运行方式：
  uv run python -m app.scripts.llm_reasoning_demo

脚本直接读取项目统一配置，不参与 Agent 图执行，也不会修改生产链路。
"""

import argparse
import asyncio
import sys

from openai import AsyncOpenAI

from app.core.config import settings


def _parse_args() -> argparse.Namespace:
    """解析 Demo 的可选问题和思考预算。"""
    parser = argparse.ArgumentParser(description="验证 LLM reasoning_content 流式返回")
    parser.add_argument(
        "--query",
        default="请分析一下：为什么查询2017年12月销售额时，需要先明确指标口径和日期筛选条件？",
        help="发送给 LLM 的测试问题",
    )
    parser.add_argument(
        "--thinking-budget",
        type=int,
        default=4096,
        help="LLM 思考过程最多使用的 token 数",
    )
    return parser.parse_args()


async def _run(query: str, thinking_budget: int) -> int:
    """发起一次思考模式流式请求并分别打印两类内容。"""
    if not settings.llm.api_key:
        print("未配置 LLM_API_KEY，无法运行 Demo。", file=sys.stderr)
        return 1
    if thinking_budget < 128:
        print("thinking_budget 不能小于 128。", file=sys.stderr)
        return 1

    client = AsyncOpenAI(
        api_key=settings.llm.api_key,
        base_url=settings.llm.base_url,
        timeout=settings.llm.timeout_seconds,
    )
    reasoning_parts: list[str] = []
    content_parts: list[str] = []
    reasoning_started = False
    content_started = False

    try:
        stream = await client.chat.completions.create(
            model=settings.llm.model_name,
            messages=[{"role": "user", "content": query}],
            stream=True,
            max_tokens=4096,
            extra_body={
                "enable_thinking": True,
                "thinking_budget": thinking_budget,
            },
        )
        print("=== reasoning_content（思考过程）===")
        async for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning = getattr(delta, "reasoning_content", None) or ""
            content = getattr(delta, "content", None) or ""

            if reasoning:
                if not reasoning_started:
                    reasoning_started = True
                reasoning_parts.append(reasoning)
                print(reasoning, end="", flush=True)
            if content:
                if not content_started:
                    if reasoning_started:
                        print("\n\n=== content（最终结果）===")
                    else:
                        print("（本次没有收到 reasoning_content）\n\n=== content（最终结果）===")
                    content_started = True
                content_parts.append(content)
                print(content, end="", flush=True)
        print("\n\n=== 汇总 ===")
        print(f"reasoning_content 字符数：{len(''.join(reasoning_parts))}")
        print(f"content 字符数：{len(''.join(content_parts))}")
        return 0
    finally:
        await client.close()


def main() -> None:
    """脚本入口。"""
    args = _parse_args()
    raise SystemExit(asyncio.run(_run(args.query, args.thinking_budget)))


if __name__ == "__main__":
    main()
