"""QueryDataTool 的输入输出契约。"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import Field

from app.agent.state_result_store.contracts import ContractModel


class QueryDataInput(ContractModel):
    """传给现有 query_graph 的最小查询输入。"""

    query: str = Field(min_length=1, max_length=20_000)


class QueryDataOutput(ContractModel):
    """从 query_graph 提取的受控查询结果摘要。"""

    # 实际写入结果 Artifact 的返回行数；可能因安全上限而小于真实总行数。
    row_count: int = Field(ge=0)
    # 查询结果字段数量。
    column_count: int = Field(ge=0)
    # 面向 Planner 的有限结果摘要，不能包含完整 rows。
    summary: str = Field(default="", max_length=8_000)
    # 是否因读取上限截断结果。
    truncated: bool = False
    # 本次查询允许读取的最大行数；未限制时为空。
    max_rows: int | None = Field(default=None, gt=0)
    # 供模型快速判断的有限预览，不是完整结果。
    preview_rows: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    sql: str = ""
    # 完整但已受行数上限约束的原始结果，进入 Artifact 而不是 ContextEngine。
    rows: list[dict[str, Any]] = Field(default_factory=list)
    display_rows: list[dict[str, Any]] = Field(default_factory=list)
    result_columns: list[dict[str, Any]] = Field(default_factory=list)
    # 查询读取阶段产生的限制，例如数据库结果被 max_rows 截断。
    limitations: list[str] = Field(default_factory=list)
    # 结果字段语义映射阶段产生的限制，与查询读取限制分开保存。
    mapping_limitations: list[str] = Field(default_factory=list)


class QueryDataPort(Protocol):
    async def execute(self, value: QueryDataInput) -> QueryDataOutput: ...


__all__ = ["QueryDataInput", "QueryDataOutput", "QueryDataPort"]
