"""FastAPI 应用入口。

这里负责创建 app、初始化日志、注册路由，以及挂载全局中间件。
"""

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from app.api.router import api_router
from app.core.config import settings
from app.core.logging import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app.name, version=settings.app.version)
app.include_router(api_router, prefix="/api")


@app.middleware("http")
async def log_requests(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """记录每一次 HTTP 请求的路径、状态码和耗时。"""
    start_time = time.perf_counter()
    response = await call_next(request)
    cost_ms = (time.perf_counter() - start_time) * 1000

    logger.info(
        "HTTP %s %s -> %s %.2fms",
        request.method,
        request.url.path,
        response.status_code,
        cost_ms,
    )
    return response


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """健康检查接口，用来确认后端服务是否正常启动。"""
    return {"status": "ok"}
