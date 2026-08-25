"""绑定真实数据并生成最终可渲染报告。

本节点不调用 LLM。它只消费 ``ReportPlan`` 和执行阶段的真实
``TaskResult``，校验数据组件引用，然后生成前端可以直接消费的
``RenderedReport``。文字组件只做最小的非空校验。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.nodes.generate_report_plan import build_report_context
from app.agent.report_plan_schema import ReportDataRef, ReportPlan, ReportPlanComponent
from app.agent.rendered_report_schema import (
    RenderedReport,
    RenderedReportColumn,
    RenderedReportComponent,
    RenderedReportSection,
)
from app.agent.state import AgentState

logger = logging.getLogger(__name__)
MAX_REPORT_ROWS = 200


def _rows(task: dict[str, Any]) -> list[dict[str, Any]]:
    """返回用于报告展示的行，优先使用维度名称已映射的副本。"""
    return task.get("display_sql_result", []) or task.get("rows", [])


def _columns(task: dict[str, Any]) -> list[dict[str, Any]]:
    """以声明字段为基础，用真实行字段补齐字段契约。"""
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


def _task_index(state: AgentState) -> dict[str, dict[str, Any]]:
    """建立报告引用使用的任务索引。"""
    context = build_report_context(state)
    if state.get("execution_mode", "single_query") == "analysis":
        tasks = state.get("analysis_task_results", [])
    else:
        task = {
            "task_id": "single_query",
            "status": "success" if state.get("sql_result") else "failed",
            "sql": state.get("sql", ""),
            "rows": state.get("sql_result", []),
            "display_sql_result": state.get("display_sql_result", []),
            "result_columns": state.get("result_columns", []),
            "calculation_result": None,
            "error": "" if state.get("sql_result") else "查询没有返回数据。",
        }
        tasks = [task]
    # context 中的状态只用于避免误把失败任务当成可绑定任务。
    logger.debug("报告渲染任务：%s", context.get("successful_task_ids", []))
    return {task["task_id"]: task for task in tasks if task.get("task_id")}


def _get_path(value: Any, path: str) -> Any:
    """按点号路径读取字典或列表中的值。"""
    current = value
    for part in filter(None, path.split(".")):
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                return None
            current = current[index]
        else:
            return None
    return current


def _render_columns(columns: list[dict[str, Any]], names: list[str]) -> list[RenderedReportColumn]:
    """将真实字段元数据裁剪为前端字段定义。"""
    by_name = {item["result_name"]: item for item in columns}
    return [
        RenderedReportColumn(
            result_name=name,
            display_name=by_name[name].get("display_name") or name,
            field_role=by_name[name].get("field_role", "unknown"),
            unit=by_name[name].get("unit"),
        )
        for name in dict.fromkeys(names)
    ]


def _base_component(plan: ReportPlanComponent) -> dict[str, Any]:
    """将规划字段转换为渲染组件的共同字段。"""
    return {
        "component_id": plan.component_id or plan.title,
        "component_type": plan.component_type,
        "chart_type": plan.chart_type,
        "title": plan.title,
        "content": plan.content,
        "source_task_id": plan.data_ref.source_task_id if plan.data_ref else "",
        "dimension_field": plan.data_ref.dimension_field if plan.data_ref else "",
        "metric_fields": plan.data_ref.metric_fields if plan.data_ref else [],
        "value_field": plan.value_field,
        "presentation": plan.presentation,
        "span": plan.span,
    }


def _failed_component(plan: ReportPlanComponent, error: str) -> RenderedReportComponent:
    """保留无法绑定的组件，方便前端和调试面板显示具体问题。"""
    return RenderedReportComponent(
        **_base_component(plan),
        binding_status="failed",
        binding_error=error,
    )


def _bind_component(
    plan: ReportPlanComponent,
    tasks: dict[str, dict[str, Any]],
) -> tuple[RenderedReportComponent, str | None]:
    """把一个 ReportPlan 组件绑定到真实任务结果。"""
    base = _base_component(plan)
    if plan.component_type == "text":
        if not plan.content.strip():
            error = f"文字组件“{plan.title}”缺少 content。"
            return _failed_component(plan, error), error
        return RenderedReportComponent(**base), None

    ref: ReportDataRef | None = plan.data_ref
    if ref is None:
        error = f"组件“{plan.title}”缺少 data_ref。"
        return _failed_component(plan, error), error
    task = tasks.get(ref.source_task_id)
    if task is None or task.get("status") != "success":
        error = f"组件“{plan.title}”引用了不存在或失败的任务“{ref.source_task_id}”。"
        return _failed_component(plan, error), error

    if ref.source == "calculation_result":
        calculation = task.get("calculation_result")
        value = _get_path(calculation, ref.path) if ref.path else calculation
        if plan.value_field:
            value = _get_path(value, plan.value_field)
        if value is None:
            error = f"组件“{plan.title}”引用了不存在的计算结果字段。"
            return _failed_component(plan, error), error
        if plan.component_type != "kpi":
            error = f"组件“{plan.title}”不能使用 calculation_result 作为 {plan.component_type} 数据源。"
            return _failed_component(plan, error), error
        return RenderedReportComponent(**base, value=value), None

    if plan.component_type == "kpi":
        error = f"KPI“{plan.title}”必须使用 calculation_result 数据源。"
        return _failed_component(plan, error), error

    all_rows = _rows(task)
    columns = _columns(task)
    by_name = {item["result_name"]: item for item in columns}
    if plan.component_type == "table":
        names = plan.requested_columns or list(by_name)
    else:
        if plan.chart_type not in {"line", "bar"}:
            error = f"图表“{plan.title}”缺少有效的 chart_type。"
            return _failed_component(plan, error), error
        names = [ref.dimension_field, *ref.metric_fields]
        if not ref.dimension_field or not ref.metric_fields:
            error = f"图表“{plan.title}”缺少维度字段或指标字段。"
            return _failed_component(plan, error), error
    unknown = [name for name in names if name not in by_name]
    if unknown:
        error = f"组件“{plan.title}”引用了不存在的字段：{unknown}。"
        return _failed_component(plan, error), error
    row_count = len(all_rows)
    rows = all_rows[:MAX_REPORT_ROWS]
    data = [{name: row.get(name) for name in dict.fromkeys(names)} for row in rows]
    return (
        RenderedReportComponent(
            **base,
            columns=_render_columns(columns, names),
            data=data,
            row_count=row_count,
            truncated=row_count > MAX_REPORT_ROWS,
        ),
        None,
    )


def _render_plan(
    plan: ReportPlan,
    tasks: dict[str, dict[str, Any]],
    evidence_status: str,
) -> RenderedReport:
    """渲染全部章节，同时保留绑定失败的组件。"""
    limitations = list(plan.limitations)
    sections: list[RenderedReportSection] = []
    has_binding_failure = False
    has_truncated = False
    for section in plan.sections:
        rendered_components: list[RenderedReportComponent] = []
        for component in section.components:
            rendered, error = _bind_component(component, tasks)
            rendered_components.append(rendered)
            has_binding_failure = has_binding_failure or error is not None
            has_truncated = has_truncated or rendered.truncated
            if error:
                limitations.append(error)
        sections.append(
            RenderedReportSection(
                title=section.title,
                layout=section.layout,
                components=rendered_components,
            )
        )
    if has_truncated:
        limitations.append(
            f"报告数据量较大，表格和图表最多展示 {MAX_REPORT_ROWS} 行；完整数据仍保留在查询结果中。"
        )
    if evidence_status == "failed":
        status = "failed"
    elif evidence_status == "partial" or has_binding_failure:
        status = "partial"
    else:
        status = "success"
    return RenderedReport(
        status=status,
        title=plan.title,
        summary=plan.summary,
        sections=sections,
        limitations=list(dict.fromkeys(item for item in limitations if item)),
    )


def _empty_rendered_report(error: str) -> RenderedReport:
    """报告规划不可用时仍返回稳定的前端结构。"""
    return RenderedReport(
        status="failed",
        title="数据分析报告",
        summary="暂时无法生成可渲染报告。",
        limitations=[error],
    )


async def render_report(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> dict[str, Any]:
    """将 ReportPlan 绑定为最终 RenderedReport。"""
    writer = runtime.stream_writer
    step = "渲染最终报告"
    node = "render_report"
    writer({"type": "progress", "step": step, "node": node, "status": "running"})
    error = state.get("report_plan_error", "")
    plan_value = state.get("report_plan", {})
    try:
        plan = ReportPlan.model_validate(plan_value)
        report = _render_plan(
            plan,
            _task_index(state),
            (state.get("analysis_evidence") or {}).get("status", "success"),
        )
        if error:
            report = report.model_copy(
                update={"status": "failed", "limitations": [*report.limitations, error]}
            )
    except Exception as exc:
        error = error or f"报告渲染失败：{exc}"
        logger.exception("报告渲染失败")
        report = _empty_rendered_report(error)

    result = report.model_dump()
    writer({
        "type": "rendered_report",
        "step": step,
        "node": node,
        "status": report.status,
        "rendered_report": result,
    })
    writer({
        "type": "progress",
        "step": step,
        "node": node,
        "status": report.status,
    })
    return {
        "rendered_report": result,
        "output_text": json.dumps(result, ensure_ascii=False, default=str),
    }

