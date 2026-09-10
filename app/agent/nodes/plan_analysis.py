"""把复杂数据问题转换为结构化分析计划。"""

import json
import logging
from typing import Annotated, Any

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field, StringConstraints, model_validator

from app.agent.context import AgentContext
from app.agent.prompts.prompt_loader import load_prompt
from app.agent.state import AgentState
from app.agent.utils.stream_timeout import astream_with_idle_timeout
from app.agent.utils.timeout_record import record_llm_timeout
from app.core.config import settings

logger = logging.getLogger(__name__)
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AnalysisTask(BaseModel):
    """分析计划中一次可交给 Query Agent 的数据查询任务。

    一个任务只描述一组查询需求；环比、排名、贡献度等计算由执行阶段
    根据 SQL 的真实返回字段生成 Python 完成。
    """

    task_id: str = Field(
        pattern=r"^[a-z][a-z0-9_]*$",
        description="任务唯一标识，使用简短且语义明确的 snake_case 名称。",
    )
    question: NonEmptyText = Field(
        description=(
            "交给 Query Agent 的自然语言查询问题，只描述需要获取的数据，不生成 SQL。"
        )
    )
    purpose: NonEmptyText = Field(
        description="该查询结果在整体分析中的用途，以及后续需要进行的确定性计算。"
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description=(
            "当前任务依赖的前置 task_id；没有依赖时返回空列表。依赖结果可作为查询条件。"
        ),
    )


class AnalysisPlan(BaseModel):
    """LLM 根据用户问题生成的最小可执行分析计划。

    当前版本只校验计划自身的结构，不负责 SQL 字段校验、SQL 重试或
    最终答案汇总。
    """

    analysis_summary: NonEmptyText = Field(
        description="概括本次分析要回答的核心问题，不提前给出分析结论。"
    )
    tasks: list[AnalysisTask] = Field(
        min_length=1,
        max_length=8,
        description="完成分析所需的最少数据查询任务，按依赖顺序排列。",
    )

    @model_validator(mode="after")
    def validate_task_dependencies(self) -> "AnalysisPlan":
        """确保任务 ID 唯一，且依赖只指向更早的任务。

        按列表顺序检查依赖，可以在 Planner 输出阶段直接拒绝未知任务、
        重复依赖和引用后置任务的计划。
        """
        seen_task_ids = set()
        for task in self.tasks:
            if task.task_id in seen_task_ids:
                raise ValueError(f"任务 ID 重复：{task.task_id}")
            if len(task.depends_on) != len(set(task.depends_on)):
                raise ValueError(f"任务 {task.task_id} 存在重复依赖")
            unknown_dependencies = set(task.depends_on) - seen_task_ids
            if unknown_dependencies:
                raise ValueError(
                    f"任务 {task.task_id} 存在无效依赖：{sorted(unknown_dependencies)}"
                )
            seen_task_ids.add(task.task_id)
        return self


async def plan_analysis(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> dict[str, Any]:
    """根据原始问题和分析目标生成查询任务及其依赖关系。"""
    writer = runtime.stream_writer
    step = "生成分析计划"
    writer({"type": "progress", "step": step, "node": "plan_analysis", "status": "running"})

    parser = PydanticOutputParser(pydantic_object=AnalysisPlan)
    prompt = PromptTemplate(
        template=load_prompt("plan_analysis"),
        input_variables=["query", "analysis_goals"],
        partial_variables={"format_instructions": parser.get_format_instructions()},
    )
    llm_client = runtime.context["llm_client"]
    input_values = {
        "query": state.get("input_text", ""),
        "analysis_goals": json.dumps(
            state.get("analysis_goals", []), ensure_ascii=False
        ),
    }
    timeout_seconds = runtime.context.get(
        "llm_timeout_seconds", settings.llm.timeout_seconds
    )
    raw_parts: list[str] = []
    reasoning_parts: list[str] = []
    reasoning_chars = 0
    content_chars = 0
    try:
        auto_stream = getattr(llm_client, "astream_auto", None)
        if auto_stream is not None:
            # AutoLLM 已将不同模型的思考和正文统一为两类事件。
            async for event in astream_with_idle_timeout(
                auto_stream(prompt.format(**input_values)),
                timeout_seconds,
            ):
                event_type = getattr(event, "event_type", "")
                event_text = getattr(event, "text", "") or ""
                if event_type == "reasoning" and event_text:
                    reasoning_parts.append(event_text)
                    reasoning_chars += len(event_text)
                    writer(
                        {
                            "type": "reasoning_chunk",
                            "step": step,
                            "node": "plan_analysis",
                            "chunk": event_text,
                            "chunk_index": len(reasoning_parts) - 1,
                            "accumulated_chars": reasoning_chars,
                        }
                    )
                elif event_type == "content" and event_text:
                    raw_parts.append(event_text)
                    content_chars += len(event_text)
                    writer(
                        {
                            "type": "llm_chunk",
                            "step": step,
                            "node": "plan_analysis",
                            "chunk": event_text,
                            "chunk_index": len(raw_parts) - 1,
                            "accumulated_chars": content_chars,
                        }
                    )
        else:
            # 保留普通 LangChain Runnable 的兼容路径。
            chain = prompt | llm_client | StrOutputParser()
            async for chunk in astream_with_idle_timeout(
                chain.astream(input_values),
                timeout_seconds,
            ):
                if not isinstance(chunk, str):
                    chunk = str(chunk)
                if not chunk:
                    continue
                raw_parts.append(chunk)
                content_chars += len(chunk)
                writer(
                    {
                        "type": "llm_chunk",
                        "step": step,
                        "node": "plan_analysis",
                        "chunk": chunk,
                        "chunk_index": len(raw_parts) - 1,
                        "accumulated_chars": content_chars,
                    }
                )
    except TimeoutError as exc:
        message = f"分析计划生成超时（连续 {timeout_seconds:g} 秒没有返回）。"
        logger.error(message)
        record_llm_timeout(
            node="plan_analysis",
            step=step,
            call_mode="stream",
            timeout_kind="stream_idle",
            timeout_seconds=timeout_seconds,
            error=message,
            llm_client=llm_client,
            writer=writer,
        )
        writer(
            {
                "type": "analysis_plan",
                "step": step,
                "node": "plan_analysis",
                "status": "failed",
                "error": message,
            }
        )
        raise TimeoutError(message) from exc
    except Exception as exc:
        logger.exception("分析计划生成失败")
        writer(
            {
                "type": "analysis_plan",
                "step": step,
                "node": "plan_analysis",
                "status": "failed",
                "error": str(exc),
            }
        )
        raise

    raw_output = "".join(raw_parts)
    reasoning = "".join(reasoning_parts)
    writer(
        {
            "type": "reasoning_result",
            "step": step,
            "node": "plan_analysis",
            "reasoning": reasoning,
            "reasoning_chars": len(reasoning),
        }
    )
    writer(
        {
            "type": "llm_result",
            "step": step,
            "node": "plan_analysis",
            "content": raw_output,
            "content_chars": len(raw_output),
        }
    )
    if not raw_output.strip():
        message = "分析计划生成 LLM 返回为空。"
        writer(
            {
                "type": "analysis_plan",
                "step": step,
                "node": "plan_analysis",
                "status": "failed",
                "error": message,
            }
        )
        raise ValueError(message)

    # 只在流结束后解析结构化计划，避免把中间增量误当作完整 JSON。
    plan = parser.parse(raw_output)
    # 将 Pydantic 计划转换成 LangGraph State 和 SSE 都能直接消费的字典。
    result = plan.model_dump()
    logger.info(
        "分析计划生成：问题=%r，摘要=%s，任务=%s",
        state.get("input_text", ""),
        result["analysis_summary"],
        result["tasks"],
    )
    writer(
        {
            "type": "analysis_plan",
            "step": step,
            "node": "plan_analysis",
            "status": "success",
            **result,
        }
    )
    return {
        "analysis_plan": result,
        "output_text": json.dumps(result, ensure_ascii=False),
    }
