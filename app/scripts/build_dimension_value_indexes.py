"""统一构建 Elasticsearch 和 Qdrant 维度值索引。

运行方式：
  uv run python -m app.scripts.build_dimension_value_indexes
"""

import json

from app.clients.mysql_client import MetaSessionLocal
from app.core.logging import setup_logging
from app.services.semantic.dimension_value_index_service import (
    build_dimension_value_indexes,
)


def main() -> None:
    """脚本入口：读取 MySQL meta 并输出两套索引构建统计。es + 向量混合检索"""
    setup_logging()
    with MetaSessionLocal() as db:
        result = build_dimension_value_indexes(db)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
