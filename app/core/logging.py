"""项目统一日志配置。

这个文件只负责配置 Python 标准 logging。业务代码需要记录日志时，使用
`logging.getLogger(__name__)` 获取当前模块的 logger 即可。
"""

import logging

from app.core.config import settings


def setup_logging() -> None:
    """初始化项目日志格式和日志级别。"""
    logging.basicConfig(
        level=settings.log_level,
        format=settings.log_format,
        force=True,
    )

    # 调整第三方库日志级别，避免开发时控制台被连接池等细节刷屏。
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
