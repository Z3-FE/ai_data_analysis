"""analyze_data 工具 —— 复用旧图分析节点的一次性深度分析适配器。

直调 plan_analysis + execute_analysis 两个节点（非图编排）：一次工具调用内完成
"分析计划 → 逐任务查询与计算 → 证据汇总"；节点进度经 stream_writer 桥接为
tool.progress；完整任务结果由 ToolRuntime 落成 analysis_result Artifact。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from app.agent.context import AgentContext
from app.agent.nodes.execute_analysis import execute_analysis
from app.agent.nodes.plan_analysis import plan_analysis
from app.agent.state import AgentState
from app.agent.state_result_store.contracts import HarnessRunRef
from app.agent.streaming.writer import HarnessEventWriter, NullHarnessEventWriter

from .contracts import AnalyzeDataInput, AnalyzeDataOutput


class AnalyzeDataTool:
    """在一次工具调用内完成分析计划、查询、计算和证据汇总。"""

    name = "analyze_data"
    input_model = AnalyzeDataInput

    def __init__(
        self,
        *,
        context: AgentContext,
        run_ref: HarnessRunRef,
        asset_ids: tuple[str, ...] = (),
        max_rows: int = 2_000,
        event_writer: HarnessEventWriter | None = None,
    ) -> None:
        if max_rows <= 0:
            raise ValueError("max_rows 必须大于 0")
        self.context = context
        self.run_ref = run_ref
        self.asset_ids = asset_ids
        self.max_rows = max_rows
        self.event_writer = event_writer or NullHarnessEventWriter(run_ref=run_ref)

    async def execute(self, value: AnalyzeDataInput) -> AnalyzeDataOutput:
        """执行已有分析节点，并把完整结果交给 ToolRuntime 的 ArtifactStore。"""
        state: AgentState = {
            "input_text": value.query,
            "original_question": value.query,
            "user_id": self.run_ref.user_id,
            "conversation_id": self.run_ref.conversation_id,
            "thread_id": self.run_ref.thread_id,
            "turn_id": self.run_ref.turn_id,
            "run_id": self.run_ref.run_id,
            "asset_ids": list(self.asset_ids),
            "analysis_goals": list(value.analysis_goals),
            "query_max_rows": self.max_rows,
        }
        # 旧图节点的依赖入口：context 取依赖，stream_writer 发进度（桥接为 tool.progress）
        runtime = SimpleNamespace(
            context=self.context,
            stream_writer=self.event_writer.custom,
        )
        # 两节点直调：先产出分析计划，再逐任务执行查询与计算
        planned = await plan_analysis(state, runtime)
        state.update(planned)
        executed = await execute_analysis(state, runtime)
        evidence = dict(executed.get("analysis_evidence") or {})
        task_results = list(executed.get("analysis_task_results") or [])
        successful = [
            item for item in task_results if item.get("status") == "success"
        ]
        failed = [item for item in task_results if item.get("status") == "failed"]
        limitations = self._limitations(task_results)
        return AnalyzeDataOutput(
            status=evidence.get("status", "failed"),
            summary=self._summary(evidence, task_results, limitations),
            analysis_summary=str(
                evidence.get("analysis_summary")
                or state.get("analysis_plan", {}).get("analysis_summary", "")
            ),
            task_count=len(task_results),
            successful_task_count=len(successful),
            failed_task_count=len(failed),
            analysis_plan=dict(state.get("analysis_plan") or {}),
            analysis_task_results=task_results,
            analysis_evidence=evidence,
            limitations=limitations,
        )

    @staticmethod
    def _limitations(task_results: list[dict[str, Any]]) -> list[str]:
        """汇总任务限制和失败原因，保持顺序去重。"""
        values: list[str] = []
        for task in task_results:
            values.extend(task.get("mapping_limitations", []) or [])
            error = str(task.get("error") or "").strip()
            if error:
                values.append(f"任务 {task.get('task_id', '')}：{error}")
        return list(dict.fromkeys(str(value) for value in values if str(value).strip()))[:32]

    @staticmethod
    def _summary(
        evidence: dict[str, Any],
        task_results: list[dict[str, Any]],
        limitations: list[str],
    ) -> str:
        """生成下一轮 Planner 可以直接消费的有限事实摘要。"""
        status = str(evidence.get("status", "failed"))
        successful_count = sum(item.get("status") == "success" for item in task_results)
        failed_count = sum(item.get("status") == "failed" for item in task_results)
        lines = [
            f"analyze_data 分析状态：{status}。",
            f"分析任务：{len(task_results)} 个，成功 {successful_count} 个，失败 {failed_count} 个。",
        ]
        analysis_summary = str(evidence.get("analysis_summary") or "").strip()
        if analysis_summary:
            lines.append(f"分析目标：{analysis_summary}")
        # 摘要总量受控：任务行最多 9 行、单条计算事实截 1200 字符、整体 7800 字符
        for task in task_results:
            if len(lines) >= 9:
                break
            calculation = task.get("calculation_result")
            if isinstance(calculation, (dict, list)):
                calculation_text = json.dumps(
                    calculation,
                    ensure_ascii=False,
                    default=str,
                    separators=(",", ":"),
                )[:1_200]
            else:
                calculation_text = str(calculation or "")[:1_200]
            line = (
                f"任务 {task.get('task_id', '')}"
                f"（{task.get('status', 'failed')}）："
                f"{task.get('purpose', '')}"
            )
            if calculation_text:
                line += f"；计算事实：{calculation_text}"
            if task.get("resolved_question"):
                line += f"；实际查询：{str(task['resolved_question'])[:500]}"
            lines.append(line)
        if limitations:
            lines.append("限制：" + "；".join(limitations[:8]))
        return "\n".join(lines)[:7_800]


__all__ = ["AnalyzeDataTool"]
