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

    row_count: int = Field(ge=0)
    column_count: int = Field(ge=0)
    sql: str = ""
    rows: list[dict[str, Any]] = Field(default_factory=list)
    display_rows: list[dict[str, Any]] = Field(default_factory=list)
    result_columns: list[dict[str, Any]] = Field(default_factory=list)
    mapping_limitations: list[str] = Field(default_factory=list)


class QueryDataPort(Protocol):
    async def execute(self, value: QueryDataInput) -> QueryDataOutput: ...


__all__ = ["QueryDataInput", "QueryDataOutput", "QueryDataPort"]
