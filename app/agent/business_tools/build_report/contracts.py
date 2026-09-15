"""Harness build_report 工具的输入和输出契约。"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import Field

from app.agent.state_result_store.contracts import ContractModel


class BuildReportInput(ContractModel):
    """报告目标和已完成数据工具的结果引用。"""

    # 本次报告的自然语言目标；同时作为报告规划的原始问题。
    goal: str = Field(min_length=1, max_length=20_000)
    # 上游 query_data 或 analyze_data 的 result_ref；报告只使用这些既有结果。
    result_refs: list[str] = Field(min_length=1, max_length=8)


class BuildReportOutput(ContractModel):
    """报告工具的完整 Artifact 内容和面向 Planner 的受控摘要。"""

    # 报告整体状态；partial 表示存在组件绑定失败或上游任务失败。
    status: Literal["success", "partial", "failed"]
    # 不包含组件数据的受控摘要，会进入 Harness Observation。
    summary: str = Field(min_length=1, max_length=8_000)
    title: str = Field(default="", max_length=200)
    section_count: int = Field(ge=0)
    component_count: int = Field(ge=0)
    failed_component_count: int = Field(ge=0)
    # 完整报告规划，只写入 Artifact，供审计和问题定位。
    report_plan: dict[str, Any] = Field(default_factory=dict)
    # 完整可渲染报告，只写入 Artifact，由收口阶段按引用取回给前端。
    rendered_report: dict[str, Any] = Field(default_factory=dict)
    # 组件绑定失败、数据截断或上游任务失败等限制说明。
    limitations: list[str] = Field(default_factory=list, max_length=32)


class BuildReportPort(Protocol):
    """Harness 对报告工具的最小异步调用边界。"""

    async def execute(self, value: BuildReportInput) -> BuildReportOutput: ...


__all__ = ["BuildReportInput", "BuildReportOutput", "BuildReportPort"]
