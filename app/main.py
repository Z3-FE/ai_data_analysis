"""FastAPI 应用入口。

这里负责创建 app、初始化日志、注册路由，以及挂载全局中间件。
"""

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response

from app.api.router import api_router
from app.clients.elasticsearch_client import elasticsearch_client_manager
from app.clients.embedding_client import embedding_client_manager
from app.clients.llm_client import llm_client_manager
from app.clients.memory_client import memory_client_manager
from app.clients.mysql_client import (
    dw_mysql_client_manager,
    meta_mysql_client_manager,
)
from app.clients.neo4j_client import neo4j_client_manager
from app.clients.postgres_client import postgres_client_manager
from app.clients.qdrant_client import qdrant_client_manager
from app.core.config import settings
from app.core.logging import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """统一管理应用级客户端的启动和关闭。"""
    llm_client_manager.init()
    embedding_client_manager.init()
    qdrant_client_manager.init()
    neo4j_client_manager.init()
    elasticsearch_client_manager.init()
    meta_mysql_client_manager.init()
    dw_mysql_client_manager.init()
    await postgres_client_manager.init()
    # 启动期校验 embedding 输出维度与 Qdrant 配置一致，切换模型导致向量空间不兼容时直接失败。
    await embedding_client_manager.verify_dimension(settings.qdrant.vector_size)
    await memory_client_manager.init()
    logger.info("应用级客户端初始化完成")
    try:
        yield
    finally:
        # 先等待记忆形成后台任务，避免它在依赖的数据库或索引服务关闭后继续写入。
        await memory_client_manager.close()
        await elasticsearch_client_manager.close()
        await qdrant_client_manager.close()
        await neo4j_client_manager.close()
        await meta_mysql_client_manager.close()
        await dw_mysql_client_manager.close()
        await postgres_client_manager.close()
        await llm_client_manager.aclose()
        logger.info("应用级客户端已关闭")


app = FastAPI(
    title=settings.app.name,
    version=settings.app.version,
    lifespan=lifespan,
)
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
