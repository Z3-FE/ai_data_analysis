"""build_report 工具 —— 上游结果到可视化报告的适配器。

凭完整运行身份从 ArtifactStore 读取 result_refs 指向的上游产物，归一化为报告任务，
再复用旧图 generate_report_plan + render_report 渲染；本次报告由 ToolRuntime
落成 rendered_report Artifact，收口时作为 final_output_ref。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.agent.context import AgentContext
from app.agent.nodes.generate_report_plan import generate_report_plan
from app.agent.nodes.render_report import render_report
from app.agent.state import AgentState
from app.agent.state_result_store.contracts import HarnessRunRef
from app.agent.streaming.writer import HarnessEventWriter, NullHarnessEventWriter
from app.agent.tool_runtime.artifacts import (
    ArtifactReadRequest,
    ArtifactRecord,
    ArtifactStoreError,
    ResultArtifactStore,
)

from .contracts import BuildReportInput, BuildReportOutput


class BuildReportTool:
    """读取上游数据结果，生成报告规划并绑定为可渲染报告。"""

    name = "build_report"
    input_model = BuildReportInput

    def __init__(
        self,
        *,
        context: AgentContext,
        run_ref: HarnessRunRef,
        artifact_store: ResultArtifactStore,
        event_writer: HarnessEventWriter | None = None,
    ) -> None:
        self.context = context
        self.run_ref = run_ref
        # 报告只读取上游结果；本次报告的持久化由 ToolRuntime 按 ToolSpec 完成。
        self.artifact_store = artifact_store
        self.event_writer = event_writer or NullHarnessEventWriter(run_ref=run_ref)

    async def execute(self, value: BuildReportInput) -> BuildReportOutput:
        """把上游结果归一化为分析证据，再复用报告规划和渲染节点。"""
        tasks: list[dict[str, Any]] = []
        taken: set[str] = set()  # 跨上游结果去重 task_id，报告组件按它引用数据
        for result_ref in dict.fromkeys(value.result_refs):
            record = await self.artifact_store.read(
                ArtifactReadRequest(run_ref=self.run_ref, result_ref=result_ref)
            )
            tasks.extend(
                self._tasks_from_artifact(record, goal=value.goal, taken=taken)
            )
        state = self._state(value, tasks)
        runtime = SimpleNamespace(
            context=self.context,
            stream_writer=self.event_writer.custom,
        )
        state.update(await generate_report_plan(state, runtime))
        rendered = await render_report(state, runtime)
        return self._output(state, rendered["rendered_report"])

    def _tasks_from_artifact(
        self,
        record: ArtifactRecord,
        *,
        goal: str,
        taken: set[str],
    ) -> list[dict[str, Any]]:
        """把一个上游结果 Artifact 转换为报告可引用的任务列表。"""
        # 只有分析结果（多任务）与查询结果（映射为单任务）可进报告，其余拒绝
        if record.artifact_kind == "analysis_result":
            source_tasks = list(record.payload.get("analysis_task_results") or [])
        elif record.artifact_kind == "query_result":
            source_tasks = [self._query_task(record.payload, goal=goal)]
        else:
            raise ArtifactStoreError(
                "artifact_kind_not_reportable",
                f"报告不支持的结果类型: {record.artifact_kind}",
                retryable=False,
            )
        tasks: list[dict[str, Any]] = []
        for index, task in enumerate(source_tasks, start=1):
            task = dict(task)
            task["task_id"] = self._unique_task_id(
                str(task.get("task_id") or f"{record.tool_name}_{index}"),
                taken=taken,
            )
            tasks.append(task)
        return tasks

    @staticmethod
    def _query_task(payload: dict[str, Any], *, goal: str) -> dict[str, Any]:
        """把单次查询结果映射为报告任务；查询没有计算结果。"""
        rows = list(payload.get("rows") or [])
        return {
            "task_id": "query",
            "status": "success" if rows else "failed",
            "question": goal,
            "purpose": goal,
            "resolved_question": goal,
            "depends_on": [],
            "sql": payload.get("sql", ""),
            "rows": rows,
            "display_sql_result": list(payload.get("display_rows") or []),
            "result_columns": list(payload.get("result_columns") or []),
            "dimension_value_mappings": [],
            "mapping_limitations": list(payload.get("mapping_limitations") or []),
            "calculation_result": None,
            "calculation_description": "",
            "error": "" if rows else "查询没有返回数据。",
        }

    @staticmethod
    def _unique_task_id(task_id: str, *, taken: set[str]) -> str:
        """报告组件按 task_id 引用数据，多个结果之间不允许重名。"""
        unique = task_id
        suffix = 2
        while unique in taken:
            unique = f"{task_id}_{suffix}"
            suffix += 1
        taken.add(unique)
        return unique

    def _state(self, value: BuildReportInput, tasks: list[dict[str, Any]]) -> AgentState:
        """构造报告节点所需的分析模式状态，不引入新的执行分支。"""
        successful = [task["task_id"] for task in tasks if task.get("status") == "success"]
        failed = [task["task_id"] for task in tasks if task.get("status") != "success"]
        if not successful:
            status = "failed"
        elif failed:
            status = "partial"
        else:
            status = "success"
        return {
            "input_text": value.goal,
            "original_question": value.goal,
            "user_id": self.run_ref.user_id,
            "conversation_id": self.run_ref.conversation_id,
            "thread_id": self.run_ref.thread_id,
            "turn_id": self.run_ref.turn_id,
            "run_id": self.run_ref.run_id,
            "execution_mode": "analysis",
            "analysis_task_results": tasks,
            "analysis_evidence": {
                "status": status,
                "analysis_summary": value.goal,
                "successful_task_ids": successful,
                "failed_task_ids": failed,
            },
        }

    @staticmethod
    def _output(state: AgentState, report: dict[str, Any]) -> BuildReportOutput:
        """生成受控摘要；完整规划和报告只进入 Artifact。"""
        sections = list(report.get("sections") or [])
        components = [
            component
            for section in sections
            for component in section.get("components") or []
        ]
        failed = [
            component
            for component in components
            if component.get("binding_status") == "failed"
        ]
        limitations = [
            str(item) for item in report.get("limitations") or [] if str(item).strip()
        ][:32]
        status = str(report.get("status", "failed"))
        title = str(report.get("title", ""))[:200]
        lines = [
            f"build_report 报告状态：{status}。",
            f"报告标题：{title}",
            f"章节 {len(sections)} 个，组件 {len(components)} 个，"
            f"绑定失败 {len(failed)} 个。",
        ]
        summary = str(report.get("summary") or "").strip()
        if summary:
            lines.append(f"报告结论：{summary}")
        if limitations:
            lines.append("限制：" + "；".join(limitations[:8]))
        return BuildReportOutput(
            status=status,
            summary="\n".join(lines)[:7_800],
            title=title,
            section_count=len(sections),
            component_count=len(components),
            failed_component_count=len(failed),
            report_plan=dict(state.get("report_plan") or {}),
            rendered_report=report,
            limitations=limitations,
        )


__all__ = ["BuildReportTool"]
