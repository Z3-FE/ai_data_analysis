"""数据仓库查询结果的内部 DTO。"""

from typing import Any

from pydantic import Field

from pydantic import BaseModel, ConfigDict


class QueryExecutionResult(BaseModel):
    """数据库返回的受控查询结果及其读取边界信息。"""

    model_config = ConfigDict(extra="forbid")

    # 本次实际返回给 Agent 的行；结果已在数据库读取阶段受 max_rows 约束。
    rows: list[dict[str, Any]] = Field(default_factory=list)
    # 数据库是否还有未返回的行；True 时不能把当前行数当作总行数。
    truncated: bool = False
    # 本次读取的最大行数。
    max_rows: int = Field(gt=0)
    # 数据库驱动返回的字段名，避免空结果丢失列结构。
    column_names: list[str] = Field(default_factory=list)


__all__ = ["QueryExecutionResult"]
