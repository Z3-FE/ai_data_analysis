"""DW 仓库。

当前项目的 DW 主要作为业务数据仓库使用，实际查询大多会由 Agent 生成 SQL
后直接执行。这里先保留仓库入口，后续如果需要固定 SQL、维表读取或样例数据
访问，再按场景补充方法。
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class DwRepository:
    """Agent 执行生成 SQL 时使用的 DW 数据仓库。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def execute_query(self, sql: str) -> list[dict[str, Any]]:
        """执行生成的查询 SQL，并返回字典行列表。"""
        result = await self.session.execute(text(sql))
        return [dict(row) for row in result.mappings().all()]
