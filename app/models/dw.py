"""dw 数据库 ORM 模型占位。

当前后端主要通过 AI 生成 SQL 查询 DW 表，业务代码还没有频繁直接操作 DW 表，
所以这里先不批量创建事实表和维度表 ORM。等后续需要固定查询或管理 DW 数据时，
再按实际使用场景补充模型。
"""

from app.models.base import Base

__all__ = ["Base"]
