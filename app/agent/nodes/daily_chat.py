"""日常聊天节点。

该节点只处理不需要访问业务数据的自然语言交流，避免问候、能力介绍等问题
进入 SQL 查询链。LLM 客户端仍由 AgentContext 注入，保持模型配置与业务节点解耦。
"""

import logging
from typing import Any

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.prompts.prompt_loader import load_prompt
from app.agent.state import AgentState

logger = logging.getLogger(__name__)


async def daily_chat(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> dict[str, Any]:
    """直接调用统一 LangChain 客户端生成普通聊天文本。"""
    writer = runtime.stream_writer
    step = "生成日常回答"
    node = "daily_chat"
    writer({"type": "progress", "step": step, "node": node, "status": "running"})

    question = state.get("input_text", "").strip()
    prompt = PromptTemplate(
        template=load_prompt("daily_chat"),
        input_variables=["query"],
    )
    chain = prompt | runtime.context["llm_client"] | StrOutputParser()
    answer = str(await chain.ainvoke({"query": question})).strip()

    logger.info("日常聊天回答完成：answer_chars=%s", len(answer))
    writer(
        {
            "type": "daily_chat",
            "step": step,
            "node": node,
            "status": "success",
            "content": answer,
        }
    )
    return {
        "output_text": answer,
        "llm_output": answer,
    }
