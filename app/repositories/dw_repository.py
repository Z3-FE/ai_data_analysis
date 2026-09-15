"""DW 仓库。

当前项目的 DW 主要作为业务数据仓库使用，实际查询大多会由 Agent 生成 SQL
后直接执行。这里先保留仓库入口，后续如果需要固定 SQL、维表读取或样例数据
访问，再按场景补充方法。
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.query_contracts import QueryExecutionResult


class DwRepository:
    """Agent 执行生成 SQL 时使用的 DW 数据仓库。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def execute_query(
        self, sql: str, *, max_rows: int = 2_000
    ) -> QueryExecutionResult:
        """执行查询，只把 max_rows + 1 行读入进程以检测截断。"""
        if max_rows <= 0:
            raise ValueError("max_rows 必须大于 0")
        result = await self.session.stream(text(sql))
        try:
            column_names = list(result.keys())
            rows = [
                dict(row)
                for row in await result.mappings().fetchmany(max_rows + 1)
            ]
            truncated = len(rows) > max_rows
            if truncated:
                rows = rows[:max_rows]
            return QueryExecutionResult(
                rows=rows,
                truncated=truncated,
                max_rows=max_rows,
                column_names=column_names,
            )
        finally:
            await result.close()
