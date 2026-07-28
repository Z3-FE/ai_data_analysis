"""数据库依赖兼容层。

MySQL 客户端已经迁移到 `app.clients.mysql_client`。这个文件保留导出，避免
现有路由导入路径立刻大面积调整。
"""

from app.clients.mysql_client import DwSessionLocal, MetaSessionLocal, get_dw_db, get_meta_db

__all__ = ["DwSessionLocal", "MetaSessionLocal", "get_dw_db", "get_meta_db"]
