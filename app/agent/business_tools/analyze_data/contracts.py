"""Harness analyze_data 工具的输入和输出契约。"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import Field

from app.agent.state_result_store.contracts import ContractModel


class AnalyzeDataInput(ContractModel):
    """传给分析计划和分析执行链的最小输入。"""

    # 当前分析任务的自然语言目标。
    query: str = Field(min_length=1, max_length=20_000)
    # 可选的分析目标；为空时由分析计划从 query 自行提取。
    analysis_goals: list[str] = Field(default_factory=list, max_length=16)


class AnalyzeDataOutput(ContractModel):
    """分析工具的完整 Artifact 内容和面向 Planner 的受控摘要。"""

    # 分析证据的整体状态；partial 表示部分任务成功。
    status: Literal["success", "partial", "failed"]
    # 不包含完整 rows 和 Python 源码的受控摘要，会进入 Harness Observation。
    summary: str = Field(min_length=1, max_length=8_000)
    # 分析计划对本次目标的概括，不提前生成未经计算的结论。
    analysis_summary: str = Field(default="", max_length=2_000)
    # 计划中的任务数量。
    task_count: int = Field(ge=0)
    # 执行成功的任务数量。
    successful_task_count: int = Field(ge=0)
    # 执行失败的任务数量。
    failed_task_count: int = Field(ge=0)
    # 完整分析计划，供 Artifact 和后续报告工具追溯。
    analysis_plan: dict[str, Any] = Field(default_factory=dict)
    # 完整任务结果，包含受行数限制的 rows 和计算结果，只写入 Artifact。
    analysis_task_results: list[dict[str, Any]] = Field(default_factory=list)
    # 精简证据集合，供后续报告和审计使用。
    analysis_evidence: dict[str, Any] = Field(default_factory=dict)
    # 查询映射、任务失败或数据截断等限制说明。
    limitations: list[str] = Field(default_factory=list, max_length=32)


class AnalyzeDataPort(Protocol):
    """Harness 对分析工具的最小异步调用边界。"""

    async def execute(self, value: AnalyzeDataInput) -> AnalyzeDataOutput: ...


__all__ = ["AnalyzeDataInput", "AnalyzeDataOutput", "AnalyzeDataPort"]
