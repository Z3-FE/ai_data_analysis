"""根据用户问题筛选本次真正需要的指标。"""

import logging

import yaml
from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.prompts.prompt_loader import load_prompt
from app.agent.state import AgentState

logger = logging.getLogger(__name__)


async def filter_metric(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> dict[str, list[str]]:
    """让 LLM 选择指标 ID，程序只保存选择结果。"""
    writer = runtime.stream_writer
    step = "过滤指标信息"
    writer({"type": "progress", "step": step, "status": "running"})

    metric_infos = state.get("metric_infos", [])
    if not metric_infos:
        return {"metric_selection": []}
    prompt = PromptTemplate(
        template=load_prompt("filter_metric_info"),
        input_variables=["query", "metric_infos"],
    )
    chain = prompt | runtime.context["llm_client"] | JsonOutputParser()
    selected_metric_ids = await chain.ainvoke(
        {
            "query": state.get("input_text", ""),
            "metric_infos": yaml.safe_dump(
                metric_infos, allow_unicode=True, sort_keys=False
            ),
        }
    )
    # LLM 一返回就记录原始内容，便于排查并行调用时到底是哪一路卡住。
    logger.info("指标过滤 LLM 原始返回：%r", selected_metric_ids)
    writer(
        {
            "type": "llm_result",
            "step": step,
            "node": "filter_metric",
            "raw_result": selected_metric_ids,
        }
    )
    if not isinstance(selected_metric_ids, list):
        raise ValueError("指标过滤结果必须是 JSON 数组。")

    writer(
        {
            "type": "filter_metric",
            "step": step,
            "status": "success",
            "selected_metric_ids": selected_metric_ids,
        }
    )
    logger.info("指标过滤选择：%s", selected_metric_ids)
    return {"metric_selection": [str(metric_id) for metric_id in selected_metric_ids]}
