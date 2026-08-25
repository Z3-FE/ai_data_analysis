"""生成最终前端报告。"""

import json
import logging
from typing import Any

from langchain_core.output_parsers import PydanticOutputParser
from langgraph.runtime import Runtime
from pydantic import ValidationError

from app.agent.context import AgentContext
from app.agent.final_report_schema import FinalReport, ReportComponent
from app.agent.prompts.prompt_loader import load_prompt
from app.agent.state import AgentState
from app.agent.utils.stream_timeout import astream_with_idle_timeout
from app.agent.utils.timeout_record import record_llm_timeout
from app.core.config import settings

logger = logging.getLogger(__name__)
MAX_REPORT_ROWS = 200
PREVIEW_ROWS = 12


def _rows(task: dict[str, Any]) -> list[dict[str, Any]]:
    """返回面向展示的结果副本。"""
    return task.get("display_sql_result", []) or task.get("rows", [])


def _columns(task: dict[str, Any]) -> list[dict[str, Any]]:
    """合并字段声明和真实返回字段。"""
    declared = {
        item["result_name"]: dict(item)
        for item in task.get("result_columns", [])
        if item.get("result_name")
    }
    for name in dict.fromkeys(key for row in _rows(task) for key in row):
        declared.setdefault(
            name,
            {"result_name": name, "display_name": name, "field_role": "unknown"},
        )
    return list(declared.values())


def _task_context(task: dict[str, Any]) -> dict[str, Any]:
    """为 LLM 构造不包含完整明细的任务上下文。"""
    rows = _rows(task)
    result = task.get("calculation_result")
    return {
        "task_id": task.get("task_id", ""),
        "status": task.get("status", "failed"),
        "purpose": task.get("purpose", ""),
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
    }


def _single_task(state: AgentState) -> dict[str, Any]:
    """把单次查询结果转换为统一任务结构。"""
    return {
        "task_id": "single_query",
        "status": "success",
        "purpose": state.get("input_text", ""),
        "resolved_question": state.get("input_text", ""),
        "sql": state.get("sql", ""),
        "rows": state.get("sql_result", []),
        "display_sql_result": state.get("display_sql_result", []),
        "result_columns": state.get("result_columns", []),
        "dimension_value_mappings": state.get("dimension_value_mappings", []),
        "mapping_limitations": state.get("mapping_limitations", []),
        "calculation_result": None,
    }


def _build_context(state: AgentState) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """构造报告上下文和真实任务索引。"""
    if state.get("execution_mode", "single_query") == "analysis":
        tasks = state.get("analysis_task_results", [])
        evidence = state.get("analysis_evidence", {})
    else:
        tasks = [_single_task(state)]
        evidence = {"status": "success", "analysis_summary": state.get("input_text", "")}
    index = {task["task_id"]: task for task in tasks if task.get("task_id")}
    context = {
        "original_question": state.get("input_text", ""),
        "analysis_summary": evidence.get("analysis_summary", ""),
        "status": evidence.get("status", "failed"),
        "successful_task_ids": evidence.get("successful_task_ids", []),
        "failed_task_ids": evidence.get("failed_task_ids", []),
        "tasks": [_task_context(task) for task in tasks],
    }
    return context, index


def _catalog() -> list[dict[str, str]]:
    """返回前端已经封装的组件白名单。"""
    return [
        {"component_type": "text", "use": "文字结论"},
        {"component_type": "kpi", "use": "计算结果中的核心值"},
        {"component_type": "table", "use": "任务结果表格"},
        {"component_type": "line_chart", "use": "趋势"},
        {"component_type": "bar_chart", "use": "分类比较或排名"},
    ]


def _bind_component(
    component: ReportComponent,
    task_index: dict[str, dict[str, Any]],
) -> tuple[ReportComponent | None, str | None]:
    """校验组件引用，并从真实任务结果绑定展示数据。"""
    component = component.model_copy(
        update={"component_id": component.component_id or component.title}
    )
    if component.component_type == "text":
        return (
            (component, None)
            if component.content.strip()
            else (None, f"文字组件“{component.title}”缺少 content。")
        )

    task = task_index.get(component.source_task_id)
    if task is None or task.get("status") != "success":
        return None, f"组件“{component.title}”引用了不存在或失败的任务。"
    columns = _columns(task)
    by_name = {item["result_name"]: item for item in columns}

    if component.component_type == "kpi":
        result = task.get("calculation_result")
        if not isinstance(result, dict) or component.value_field not in result:
            return None, f"KPI“{component.title}”引用了不存在的计算结果字段。"
        return component.model_copy(update={"value": result[component.value_field]}), None

    all_rows = _rows(task)
    names = component.requested_columns or []
    if component.component_type == "table":
        names = names or list(by_name)
    else:
        if not component.dimension or component.dimension not in by_name:
            return None, f"图表“{component.title}”引用了不存在的维度字段。"
        if not component.metrics:
            return None, f"图表“{component.title}”没有指标字段。"
        names = [component.dimension, *component.metrics]
    unknown = [name for name in names if name not in by_name]
    if unknown:
        return None, f"组件“{component.title}”引用了不存在的字段：{unknown}。"
    row_count = len(all_rows)
    rows = all_rows[:MAX_REPORT_ROWS]
    data = [{name: row.get(name) for name in names} for row in rows]
    return component.model_copy(
        update={
            "columns": [by_name[name] for name in dict.fromkeys(names)],
            "data": data,
            "row_count": row_count,
            "truncated": row_count > MAX_REPORT_ROWS,
        }
    ), None


def _bind_report(
    draft: FinalReport,
    task_index: dict[str, dict[str, Any]],
    evidence_status: str,
) -> FinalReport:
    """移除无效组件，并汇总组件绑定产生的限制。"""
    limitations = list(draft.limitations)
    sections = []
    dropped = False
    truncated = False
    for section in draft.sections:
        components = []
        for component in section.components:
            bound, error = _bind_component(component, task_index)
            if bound is not None:
                components.append(bound)
                truncated = truncated or bound.truncated
            else:
                dropped = True
                if error:
                    limitations.append(error)
        if components:
            sections.append(section.model_copy(update={"components": components}))
    if truncated:
        limitations.append(
            f"报告数据量较大，表格和图表最多展示 {MAX_REPORT_ROWS} 行；完整数据请通过查询获取。"
        )
    status = evidence_status if evidence_status in {"success", "partial"} else "failed"
    if dropped and status == "success":
        status = "partial"
    return draft.model_copy(
        update={
            "status": status,
            "sections": sections,
            "limitations": list(dict.fromkeys(item for item in limitations if item)),
        }
    )


async def generate_final_report(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> dict[str, Any]:
    """流式调用最终报告 LLM，并在结束后绑定真实数据。"""
    writer = runtime.stream_writer
    step = "生成最终报告"
    writer({"type": "progress", "step": step, "node": "generate_final_report", "status": "running"})
    context, task_index = _build_context(state)
    if not any(task.get("status") == "success" for task in task_index.values()):
        report = FinalReport(
            status="failed",
            title="数据分析报告",
            summary="没有成功完成的数据任务，暂时无法生成可靠报告。",
            limitations=["本次分析没有可用于报告的数据证据。"],
        )
    else:
        parser = PydanticOutputParser(pydantic_object=FinalReport)
        values = {
            "original_question": context["original_question"],
            "report_context": json.dumps(context, ensure_ascii=False, default=str),
            "component_catalog": json.dumps(_catalog(), ensure_ascii=False),
            "format_instructions": parser.get_format_instructions(),
        }
        raw_parts: list[str] = []
        try:
            timeout_seconds = runtime.context.get(
                "llm_timeout_seconds", settings.llm.timeout_seconds
            )
            template = load_prompt("generate_final_report").format(**values)
            async for chunk in astream_with_idle_timeout(
                runtime.context["llm_client"].astream(template),
                timeout_seconds,
            ):
                text = getattr(chunk, "content", chunk)
                if not isinstance(text, str):
                    continue
                raw_parts.append(text)
                writer({"type": "report_text_delta", "step": step, "node": "generate_final_report", "chunk": text})
            raw = "".join(raw_parts).strip()
            start, end = raw.find("{"), raw.rfind("}")
            raw = raw[start : end + 1] if start >= 0 and end > start else raw
            report = _bind_report(
                FinalReport.model_validate(json.loads(raw)),
                task_index,
                context["status"],
            )
        except TimeoutError as exc:
            logger.exception("最终报告流式响应空闲超时")
            record_llm_timeout(
                node="generate_final_report",
                step=step,
                call_mode="stream",
                timeout_kind="stream_idle",
                timeout_seconds=timeout_seconds,
                error=exc,
                llm_client=runtime.context["llm_client"],
                writer=writer,
            )
            report = FinalReport(
                status="failed",
                title="数据分析报告",
                summary="最终报告生成超时，暂时无法返回可渲染报告。",
                limitations=[f"最终报告生成超时：{exc}"],
            )
        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            logger.exception("最终报告生成或解析失败")
            report = FinalReport(
                status="failed",
                title="数据分析报告",
                summary="最终报告生成失败，暂时无法返回可渲染报告。",
                limitations=[f"报告模型输出无效：{exc}"],
            )
        except Exception as exc:
            logger.exception("最终报告 LLM 调用失败")
            report = FinalReport(
                status="failed",
                title="数据分析报告",
                summary="最终报告生成失败，暂时无法返回可渲染报告。",
                limitations=[f"报告模型调用失败：{exc}"],
            )
    result = report.model_dump()
    writer({"type": "final_report", "step": step, "node": "generate_final_report", "status": report.status, "report": result})
    return {
        "final_report": result,
        "output_text": json.dumps(result, ensure_ascii=False, default=str),
    }
