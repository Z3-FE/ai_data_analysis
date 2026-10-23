"""项目统一日志配置。

这个文件只负责配置 Python 标准 logging。业务代码需要记录日志时，使用
`logging.getLogger(__name__)` 获取当前模块的 logger 即可。
"""

import logging
import os
import sys

from app.core.config import settings


class ConsoleFormatter(logging.Formatter):
    """只改变控制台显示，不改写 LogRecord 内容。"""

    def __init__(self, fmt: str, *, use_color: bool) -> None:
        super().__init__(fmt)
        self.use_color = use_color

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if self.use_color and record.levelno == logging.INFO:
            return f"\033[36m{text}\033[0m"
        return text


def setup_logging() -> None:
    """初始化项目日志格式和日志级别。"""
    handler = logging.StreamHandler()
    handler.setFormatter(
        ConsoleFormatter(
            settings.logging.format,
            use_color=sys.stderr.isatty() and "NO_COLOR" not in os.environ,
        )
    )
    logging.basicConfig(
        level=settings.logging.level,
        handlers=[handler],
        force=True,
    )

    # 调整第三方库日志级别，避免开发时控制台被连接池等细节刷屏。
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
