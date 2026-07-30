"""统一构建 Elasticsearch 和 Qdrant 维度值索引。

运行方式：
  uv run python -m app.scripts.build_dimension_value_indexes
"""

import asyncio
import json

from app.clients.mysql_client import meta_mysql_client_manager
from app.core.logging import setup_logging
from app.services.semantic.dimension_value_index_service import (
    build_dimension_value_indexes,
)


def _require_initialized(resource, resource_name: str):
    """确保脚本级资源已经初始化。"""
    if resource is None:
        raise RuntimeError(f"{resource_name}尚未初始化")
    return resource


def main() -> None:
    """脚本入口：读取 MySQL meta 并输出两套索引构建统计。"""
    setup_logging()
    meta_mysql_client_manager.init()
    try:
        session_factory = _require_initialized(
            meta_mysql_client_manager.session_factory,
            "Meta MySQL Session 工厂",
        )

        async def _run():
            async with session_factory() as session:
                return await session.run_sync(build_dimension_value_indexes)

        result = asyncio.run(_run())
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        asyncio.run(meta_mysql_client_manager.close())


if __name__ == "__main__":
    main()
