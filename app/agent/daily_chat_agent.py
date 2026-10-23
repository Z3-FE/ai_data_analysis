"""Harness 使用的轻量日常聊天分支。"""

from typing import Any


class DailyChatAgent:
    """消费 Harness 已编译的聊天上下文，不依赖旧 LangGraph 状态。"""

    def __init__(self, llm_client: Any) -> None:
        self.llm_client = llm_client

    async def answer(self, *, messages: tuple[dict[str, Any], ...]) -> str:
        # 直接使用经过预算控制的完整上下文，不重复追加问题或丢弃历史与摘要。
        response = await self.llm_client.ainvoke_auto(list(messages))
        return response.content.strip()


def is_greeting(query: str) -> bool:
    """纯问候直接进入日常聊天，避免被 Planner 当成缺少条件。"""
    normalized = "".join(query.lower().split()).strip("!！,，。.?？")
    return normalized in {
        "你好", "您好", "嗨", "哈喽", "hello", "hi", "hey", "早上好", "晚上好", "谢谢"
    }


__all__ = ["DailyChatAgent", "is_greeting"]
