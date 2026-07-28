"""数据库 ORM 模型包。

models 只描述数据库表结构，不承载业务编排逻辑。
"""

from app.models.base import Base

__all__ = ["Base"]
