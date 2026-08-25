"""最终数据分析报告的唯一前端通信结构。

LLM 只负责生成报告文字和组件引用；``data``、字段定义和 KPI 值由后端
根据真实 TaskResult 绑定，避免模型重新编造查询结果。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ReportSection(BaseModel):
    """最终报告中的一个章节。"""

    # 章节标题由最终报告 LLM 根据用户问题和证据组织。
    title: str = Field(min_length=1)
    # 章节内组件的排列顺序就是前端的渲染顺序。
    components: list["ReportComponent"] = Field(default_factory=list)


class ReportComponent(BaseModel):
    """前端白名单组件及其真实数据绑定结果。"""

    # 前端使用该 ID 作为稳定的 React key 和组件追踪标识。
    component_id: str = Field(default="")
    # 只能使用前端已封装的组件类型，禁止 LLM 生成任意组件代码。
    component_type: Literal["text", "kpi", "table", "line_chart", "bar_chart"]
    # 面向用户的组件标题。
    title: str = Field(min_length=1)
    # 文本组件的正文；其他组件通常为空。
    content: str = ""
    # 表格、图表或 KPI 绑定的成功分析任务 ID。
    source_task_id: str = ""
    # 图表横轴或分类维度的真实 result_name。
    dimension: str = ""
    # 图表绑定的真实指标 result_name；LLM 只能从可用字段中选择。
    metrics: list[str] = Field(default_factory=list)
    # 表格希望展示的真实 result_name；为空时由程序使用全部可用字段。
    requested_columns: list[str] = Field(default_factory=list)
    # KPI 需要读取的 calculation_result 字段名。
    value_field: str = ""

    # 以下字段由程序绑定真实数据，LLM 不应在输出中填写。
    # KPI 的真实值，或绑定失败时保持 None。
    value: Any = None
    # 表格和图表最终绑定的展示字段定义。
    columns: list[dict[str, Any]] = Field(default_factory=list)
    # 表格或图表实际展示的数据行，使用 display_sql_result 的展示名称。
    data: list[dict[str, Any]] = Field(default_factory=list)
    # 后端任务的完整结果行数，不等于本次返回的 data 行数。
    row_count: int = Field(default=0, ge=0)
    # 数据量过大时只返回前 MAX_REPORT_ROWS 行，前端据此展示提示。
    truncated: bool = False


class FinalReport(BaseModel):
    """最终交给前端渲染的数据分析报告。"""

    # 报告生成状态由程序根据证据和数据绑定结果决定，LLM 不能自行美化状态。
    status: Literal["success", "partial", "failed"] = "success"
    # 报告标题和摘要由最终报告 LLM 生成。
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    # 章节和组件顺序由 LLM 规划，数据字段由程序在生成后绑定。
    sections: list[ReportSection] = Field(default_factory=list)
    # 证据不足、任务失败、值映射缺失或数据截断等边界说明。
    limitations: list[str] = Field(default_factory=list)

