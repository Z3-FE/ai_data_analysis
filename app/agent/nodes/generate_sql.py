"""根据过滤后的额外上下文生成 SQL。

当前节点只负责生成 SQL 文本，不负责 SQL AST 校验、只读校验、执行或重试。
这些逻辑留到后续 SQL 闭环阶段实现。
"""

import json
import logging
from typing import Any

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.prompts.prompt_loader import load_prompt
from app.agent.state import AgentState

logger = logging.getLogger(__name__)


def _clean_sql(sql: str) -> str:
    """去掉模型偶尔附带的 Markdown 代码块标记，保留 SQL 本身。"""
    cleaned = sql.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = cleaned[3:-3].strip()
        if cleaned.lower().startswith("sql"):
            cleaned = cleaned[3:].strip()
    return cleaned


async def generate_sql(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> dict[str, Any]:
    """把用户问题和额外上下文交给 LLM，返回生成的 SQL。"""
    writer = runtime.stream_writer
    step = "生成 SQL"
    writer({"type": "progress", "step": step, "status": "running"})

    prompt = PromptTemplate(
        template=load_prompt("generate_sql"),
        input_variables=["query", "extra_context"],
    )
    chain = prompt | runtime.context["llm_client"] | StrOutputParser()
    sql = await chain.ainvoke(
        {
            "query": state.get("input_text", ""),
            "extra_context": json.dumps(
                state.get("extra_context", {}),
                ensure_ascii=False,
                indent=2,
            ),
        }
    )
    logger.info("SQL 生成 LLM 原始返回：%r", sql)
    writer(
        {
            "type": "llm_result",
            "step": step,
            "node": "generate_sql",
            "raw_result": sql,
        }
    )
    sql = _clean_sql(sql)
    writer(
        {
            "type": "generate_sql",
            "step": step,
            "status": "success",
            "sql": sql,
        }
    )
    return {"sql": sql}
