"""后端绑定真实数据后的前端报告结构。"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.agent.report_plan_schema import ChartPresentation, ReportLayout


class RenderedReportColumn(BaseModel):
    """组件实际使用的结果字段定义。"""

    result_name: str
    display_name: str = ""
    field_role: str = "unknown"
    unit: str | None = None


class RenderedReportComponent(BaseModel):
    """前端可直接渲染的组件及其绑定状态。"""

    component_id: str
    component_type: Literal["text", "kpi", "table", "chart"]
    chart_type: Literal["line", "bar"] | None = None
    title: str
    content: str = ""
    source_task_id: str = ""
    dimension_field: str = ""
    metric_fields: list[str] = Field(default_factory=list)
    value_field: str = ""
    presentation: ChartPresentation = Field(default_factory=ChartPresentation)
    span: int = Field(default=1, ge=1, le=4)
    binding_status: Literal["bound", "failed"] = "bound"
    binding_error: str = ""
    value: Any = None
    columns: list[RenderedReportColumn] = Field(default_factory=list)
    data: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = Field(default=0, ge=0)
    truncated: bool = False


class RenderedReportSection(BaseModel):
    """前端报告章节和布局。"""

    title: str
    layout: ReportLayout = Field(default_factory=ReportLayout)
    components: list[RenderedReportComponent] = Field(default_factory=list)


class RenderedReport(BaseModel):
    """最终交给前端的唯一报告结构。"""

    status: Literal["success", "partial", "failed"] = "success"
    title: str
    summary: str
    sections: list[RenderedReportSection] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

