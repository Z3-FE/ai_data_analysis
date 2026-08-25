"""查询结果字段契约。

结果字段契约连接 SQL 生成、结果增强、分析计算和展示产物：基础字段和
基础指标由 Meta 元数据提供来源；临时派生字段由当前任务声明来源。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class FilterCondition(BaseModel):
    """派生结果字段使用的一条可追溯筛选条件。"""

    column_id: str = Field(min_length=1, description="筛选字段的完整 Meta column_id。")
    operator: str = Field(min_length=1, description="筛选运算符，例如 =、IN、>=。")
    raw_value: Any = Field(description="SQL 使用的数据库原始筛选值。")


class ResultColumn(BaseModel):
    """SQL 或 Python 结果中的一个字段及其来源说明。"""

    # result_name 必须和实际结果行中的 key 一致，才能进行程序校验。
    result_name: str = Field(min_length=1, description="结果行中的字段名。")
    # unknown 兼容无法从当前元数据确定来源的临时字段。
    field_role: Literal["dimension", "metric", "derived_metric", "unknown"] = "unknown"

    @field_validator("field_role", mode="before")
    @classmethod
    def normalize_field_role(cls, value: str) -> str:
        """兼容 LLM 使用 measure 表示普通指标的写法。"""
        return "metric" if value == "measure" else value

    # 基础字段、指标或派生字段的面向用户名称。
    display_name: str = ""
    # 已登记物理字段的来源。
    source_column_id: str = ""
    # 已登记指标或派生指标所依赖的基础指标。
    source_metric_id: str = ""
    # 派生字段依赖的当前结果字段。
    source_fields: list[str] = Field(default_factory=list)
    # 派生指标使用的筛选条件，保存原始值而不是展示名称。
    filter_conditions: list[FilterCondition] = Field(default_factory=list)
    # 标识展示名称来自正式 Meta、当前 LLM 声明还是原始字段名。
    display_name_source: Literal["meta", "llm_declared", "raw"] = "raw"
    # 标识字段血缘是否已经由 Meta 确认。
    lineage_status: Literal["matched", "declared", "unknown"] = "unknown"


class SqlGenerationResult(BaseModel):
    """SQL 生成节点的结构化输出。"""

    sql: str = Field(min_length=1, description="只读查询 SQL。")
    result_columns: list[ResultColumn] = Field(
        default_factory=list,
        description="最终 SELECT 返回字段的来源和展示契约。",
    )
