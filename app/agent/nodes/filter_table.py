"""根据用户问题筛选本次真正需要的表和业务字段。"""

import logging

import yaml
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.prompts.prompt_loader import load_prompt
from app.agent.state import AgentState

logger = logging.getLogger(__name__)


async def filter_table(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> dict[str, dict[str, list[str]]]:
    """让 LLM 选择表和字段 ID，程序在后续节点完成裁剪和补全。"""
    writer = runtime.stream_writer
    step = "过滤表字段信息"
    writer({"type": "progress", "step": step, "node": "filter_table", "status": "running"})

    table_infos = state.get("table_infos", [])
    if not table_infos:
        writer(
            {
                "type": "filter_table",
                "step": step,
                "node": "filter_table",
                "status": "success",
                "selected_tables": {},
            }
        )
        return {"table_selection": {}}
    prompt = PromptTemplate(
        template=load_prompt("filter_table_info"),
        input_variables=["query", "table_infos"],
    )
    chain = prompt | runtime.context["llm_client"] | JsonOutputParser()
    selected_tables = await chain.ainvoke(
        {
            "query": state.get("input_text", ""),
            "table_infos": yaml.safe_dump(
                table_infos, allow_unicode=True, sort_keys=False
            ),
        }
    )
    # LLM 一返回就记录原始内容，便于排查并行调用时到底是哪一路卡住。
    logger.info("表字段过滤 LLM 原始返回：%r", selected_tables)
    writer(
        {
            "type": "llm_result",
            "step": step,
            "node": "filter_table",
            "raw_result": selected_tables,
        }
    )
    if selected_tables == []:
        selected_tables = {}
    if not isinstance(selected_tables, dict):
        raise ValueError("表字段过滤结果必须是 JSON 对象。")

    writer(
        {
            "type": "filter_table",
            "step": step,
            "node": "filter_table",
            "status": "success",
            "selected_tables": selected_tables,
        }
    )
    logger.info("表字段过滤选择：%s", selected_tables)
    return {
        "table_selection": {
            str(table_id): [str(column_id) for column_id in column_ids]
            for table_id, column_ids in selected_tables.items()
        }
    }
