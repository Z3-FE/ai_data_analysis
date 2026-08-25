"""生成报告规划。

本节点只负责把分析证据交给 LLM，生成报告的文字、组件引用和布局意图。
它不读取完整 rows，也不绑定任何前端组件数据；真实数据绑定由
``render_report`` 节点完成。
"""

from __future__ import annotations

import json
import logging
from json import JSONDecodeError
from typing import Any

from langchain_core.output_parsers import PydanticOutputParser
from langgraph.runtime import Runtime
from pydantic import ValidationError

from app.agent.context import AgentContext
from app.agent.prompts.prompt_loader import load_prompt
from app.agent.report_plan_schema import ReportPlan
from app.agent.state import AgentState
from app.agent.utils.stream_timeout import astream_with_idle_timeout
from app.agent.utils.timeout_record import record_llm_timeout
from app.core.config import settings

logger = logging.getLogger(__name__)
PREVIEW_ROWS = 12


def _rows(task: dict[str, Any]) -> list[dict[str, Any]]:
    """返回用于报告上下文的展示结果。"""
    return task.get("display_sql_result", []) or task.get("rows", [])


def _columns(task: dict[str, Any]) -> list[dict[str, Any]]:
    """合并 SQL 声明字段和真实返回字段。"""
    declared = {
        item["result_name"]: dict(item)
        for item in task.get("result_columns", [])
        if item.get("result_name")
    }
    for name in dict.fromkeys(key for row in _rows(task) for key in row):
        declared.setdefault(
            name,
            {
                "result_name": name,
                "display_name": name,
                "field_role": "unknown",
            },
        )
    return list(declared.values())


def _single_task(state: AgentState) -> dict[str, Any]:
    """把单次查询状态转换为报告任务上下文。"""
    return {
        "task_id": "single_query",
        "status": "success" if state.get("sql_result") else "failed",
        "question": state.get("input_text", ""),
        "purpose": state.get("input_text", ""),
        "resolved_question": state.get("input_text", ""),
        "depends_on": [],
        "sql": state.get("sql", ""),
        "rows": state.get("sql_result", []),
        "display_sql_result": state.get("display_sql_result", []),
        "result_columns": state.get("result_columns", []),
        "dimension_value_mappings": state.get("dimension_value_mappings", []),
        "mapping_limitations": state.get("mapping_limitations", []),
        "calculation_result": None,
        "calculation_description": "",
        "error": "" if state.get("sql_result") else "查询没有返回数据。",
    }


def _task_context(task: dict[str, Any]) -> dict[str, Any]:
    """创建不包含完整 rows 的 LLM 任务上下文。"""
    rows = _rows(task)
    result = task.get("calculation_result")
    return {
        "task_id": task.get("task_id", ""),
        "status": task.get("status", "failed"),
        "question": task.get("question", ""),
        "purpose": task.get("purpose", ""),
        "depends_on": task.get("depends_on", []),
        "resolved_question": task.get("resolved_question", ""),
        "sql": task.get("sql", ""),
        "row_count": len(rows),
        "result_columns": _columns(task),
        "calculation_description": task.get("calculation_description", ""),
        "calculation_result": result,
        "calculation_fields": sorted(result) if isinstance(result, dict) else [],
        "dimension_value_mappings": task.get("dimension_value_mappings", []),
        "mapping_limitations": task.get("mapping_limitations", []),
        "preview_rows": rows[:PREVIEW_ROWS],
        "error": task.get("error", ""),
    }


def build_report_context(state: AgentState) -> dict[str, Any]:
    """为报告规划 LLM 构造受控上下文。"""
    if state.get("execution_mode", "single_query") == "analysis":
        tasks = state.get("analysis_task_results", [])
        evidence = state.get("analysis_evidence", {})
    else:
        tasks = [_single_task(state)]
        evidence = {
            "status": "success" if tasks[0]["status"] == "success" else "failed",
            "analysis_summary": state.get("input_text", ""),
            "successful_task_ids": ["single_query"] if tasks[0]["status"] == "success" else [],
            "failed_task_ids": [] if tasks[0]["status"] == "success" else ["single_query"],
        }
    return {
        "original_question": state.get("input_text", ""),
        "analysis_summary": evidence.get("analysis_summary", ""),
        "status": evidence.get("status", "failed"),
        "successful_task_ids": evidence.get("successful_task_ids", []),
        "failed_task_ids": evidence.get("failed_task_ids", []),
        "tasks": [_task_context(task) for task in tasks],
    }


def _component_catalog() -> list[dict[str, Any]]:
    """描述前端可调用的组件协议，而不是暴露具体渲染代码。"""
    return [
        {"component_type": "text", "use": "文字结论"},
        {"component_type": "kpi", "use": "calculation_result 中的核心值"},
        {"component_type": "table", "use": "任务 rows 的字段表格"},
        {"component_type": "chart", "chart_type": ["line", "bar"], "use": "任务 rows 的趋势或分类比较"},
    ]


def _extract_json(raw: str) -> str:
    """从模型可能附带的 Markdown 或解释中取出第一个 JSON 对象。"""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(line for line in lines if not line.strip().startswith("```")).strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            _, end = decoder.raw_decode(text[index:])
        except JSONDecodeError:
            continue
        return text[index : index + end]
    raise ValueError("报告规划模型没有返回 JSON 对象。")


async def _stream_plan(
    prompt: str,
    runtime: Runtime[AgentContext],
    writer: Any,
    step: str,
) -> tuple[str, str]:
    """统一消费 AutoLLM 的 reasoning/content 流。"""
    llm_client = runtime.context["llm_client"]
    timeout_seconds = runtime.context.get("llm_timeout_seconds", settings.llm.timeout_seconds)
    raw_parts: list[str] = []
    reasoning_parts: list[str] = []
    auto_stream = getattr(llm_client, "astream_auto", None)
    if auto_stream is not None:
        stream = auto_stream(prompt)
        async for event in astream_with_idle_timeout(stream, timeout_seconds):
            event_type = getattr(event, "event_type", "")
            event_text = getattr(event, "text", "") or ""
            if event_type == "reasoning" and event_text:
                reasoning_parts.append(event_text)
                writer({
                    "type": "reasoning_chunk",
                    "step": step,
                    "node": "generate_report_plan",
                    "chunk": event_text,
                    "chunk_index": len(reasoning_parts) - 1,
                })
            elif event_type == "content" and event_text:
                raw_parts.append(event_text)
                writer({
                    "type": "llm_chunk",
                    "step": step,
                    "node": "generate_report_plan",
                    "chunk": event_text,
                    "chunk_index": len(raw_parts) - 1,
                })
    else:
        # 普通 LangChain Runnable 也直接接收已经格式化完成的提示词，避免
        # JSON 格式说明中的大括号再次被 PromptTemplate 当成变量解析。
        async for chunk in astream_with_idle_timeout(llm_client.astream(prompt), timeout_seconds):
            text = getattr(chunk, "content", chunk)
            text = text if isinstance(text, str) else str(text)
            if text:
                raw_parts.append(text)
                writer({
                    "type": "llm_chunk",
                    "step": step,
                    "node": "generate_report_plan",
                    "chunk": text,
                    "chunk_index": len(raw_parts) - 1,
                })
    return "".join(raw_parts), "".join(reasoning_parts)


def _failed_plan(question: str) -> dict[str, Any]:
    """模型失败时返回可继续渲染的空规划。"""
    return ReportPlan(
        title="数据分析报告",
        summary="暂时无法生成报告规划。",
        sections=[],
        limitations=[f"报告规划未生成：{question}"],
    ).model_dump()


async def generate_report_plan(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> dict[str, Any]:
    """流式调用 LLM 生成只包含规划意图的 ReportPlan。"""
    writer = runtime.stream_writer
    step = "生成报告规划"
    node = "generate_report_plan"
    writer({"type": "progress", "step": step, "node": node, "status": "running"})
    context = build_report_context(state)
    has_success = bool(context["successful_task_ids"])
    plan_result = _failed_plan(context["original_question"])
    plan_status = "failed"
    error_message = ""
    reasoning = ""
    raw_output = ""
    timeout_seconds = runtime.context.get("llm_timeout_seconds", settings.llm.timeout_seconds)

    if has_success:
        parser = PydanticOutputParser(pydantic_object=ReportPlan)
        values = {
            "original_question": context["original_question"],
            "report_context": json.dumps(context, ensure_ascii=False, default=str),
            "component_catalog": json.dumps(_component_catalog(), ensure_ascii=False),
            "format_instructions": parser.get_format_instructions(),
        }
        try:
            template = load_prompt("generate_report_plan")
            raw_output, reasoning = await _stream_plan(
                template.format(**values), runtime, writer, step
            )
            writer({"type": "reasoning_result", "step": step, "node": node, "reasoning": reasoning, "reasoning_chars": len(reasoning)})
            writer({"type": "llm_result", "step": step, "node": node, "content": raw_output, "content_chars": len(raw_output)})
            plan = parser.parse(_extract_json(raw_output))
            plan_result = plan.model_dump()
            plan_status = "success"
        except TimeoutError as exc:
            error_message = f"报告规划生成超时（连续 {timeout_seconds:g} 秒没有返回）。"
            logger.error(error_message)
            record_llm_timeout(
                node=node, step=step, call_mode="stream", timeout_kind="stream_idle",
                timeout_seconds=timeout_seconds, error=error_message,
                llm_client=runtime.context.get("llm_client"), writer=writer,
            )
        except (ValidationError, JSONDecodeError, ValueError) as exc:
            error_message = f"报告规划输出无效：{exc}"
            logger.exception("报告规划解析失败")
        except Exception as exc:
            error_message = f"报告规划模型调用失败：{exc}"
            logger.exception("报告规划生成失败")
    else:
        error_message = "没有成功完成的数据任务，暂时无法生成报告规划。"

    if error_message:
        plan_result["limitations"] = [error_message]
    writer({
        "type": "report_plan_result",
        "step": step,
        "node": node,
        "status": plan_status,
        "report_plan": plan_result,
        "error": error_message,
    })
    if plan_status == "success":
        writer({"type": "progress", "step": step, "node": node, "status": "success"})
    else:
        writer({"type": "progress", "step": step, "node": node, "status": "failed", "error": error_message})
    return {
        "report_plan": plan_result,
        "report_plan_status": plan_status,
        "report_plan_error": error_message,
        "output_text": json.dumps(plan_result, ensure_ascii=False, default=str),
    }
