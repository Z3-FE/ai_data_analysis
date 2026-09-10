"""LLM 生成的报告规划结构。"""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ReportLayout(BaseModel):
    """章节内组件的布局意图。"""

    type: Literal["stack", "grid"] = "stack"
    columns: int = Field(default=1, ge=1, le=4)


class ReportDataRef(BaseModel):
    """LLM 对真实任务数据的引用，不包含真实数据。"""

    source_task_id: str = Field(min_length=1)
    source: Literal["rows", "calculation_result"] = "rows"
    path: str = ""
    dimension_field: str = ""
    metric_fields: list[str] = Field(default_factory=list)


class ChartPresentation(BaseModel):
    """面向前端图表组件的语义化配置。"""

    orientation: Literal["horizontal", "vertical"] = "vertical"
    sort: Literal["asc", "desc", "none"] = "none"
    top_n: int | None = Field(default=None, ge=1, le=100)
    show_labels: bool = True
    show_legend: bool = False
    color_scheme: str = "blue"


class ReportPlanComponent(BaseModel):
    """LLM 希望前端渲染的一个组件。"""

    component_id: str = ""
    component_type: Literal["text", "kpi", "table", "chart"]
    chart_type: Literal["line", "bar"] | None = None
    title: str = Field(min_length=1)
    content: str = ""
    data_ref: ReportDataRef | None = None
    value_field: str = ""
    requested_columns: list[str] = Field(default_factory=list)
    presentation: ChartPresentation = Field(default_factory=ChartPresentation)
    span: int = Field(default=1, ge=1, le=4)


class ReportPlanSection(BaseModel):
    """LLM 规划的一个报告章节。"""

    title: str = Field(min_length=1)
    layout: ReportLayout = Field(default_factory=ReportLayout)
    components: list[ReportPlanComponent] = Field(default_factory=list)


class ReportPlan(BaseModel):
    """报告规划节点的输出。"""

    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    sections: list[ReportPlanSection] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

