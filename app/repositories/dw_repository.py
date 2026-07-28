"""DW 仓库。

当前项目的 DW 主要作为业务数据仓库使用，实际查询大多会由 Agent 生成 SQL
后直接执行。这里先保留仓库入口，后续如果需要固定 SQL、维表读取或样例数据
访问，再按场景补充方法。
"""

from sqlalchemy.orm import Session


class DwRepository:
    """DW 数据访问入口占位。"""

    def __init__(self, db: Session) -> None:
        self.db = db
