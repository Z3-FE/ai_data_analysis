"""统一元数据向量构建脚本测试。"""

import unittest
from unittest.mock import Mock, patch

from app.scripts.build_meta_vectors import build_all_meta_vectors


class BuildMetaVectorsTest(unittest.TestCase):
    """验证三类向量按顺序调用并正确汇总数量。"""

    @patch("app.scripts.build_meta_vectors.build_meta_metric_vectors")
    @patch("app.scripts.build_meta_vectors.build_meta_column_vectors")
    @patch("app.scripts.build_meta_vectors.build_meta_table_vectors")
    def test_builds_and_summarizes_all_collections(
        self,
        build_tables: Mock,
        build_columns: Mock,
        build_metrics: Mock,
    ) -> None:
        build_tables.return_value = {"point_count": 60, "qdrant_count": 60}
        build_columns.return_value = {"point_count": 56, "qdrant_count": 56}
        build_metrics.return_value = {"point_count": 40, "qdrant_count": 40}
        db = object()
        embedding_client = object()
        qdrant_repository = object()

        result = build_all_meta_vectors(
            db,
            embedding_client=embedding_client,
            qdrant_repository=qdrant_repository,
        )

        self.assertEqual(result["total_point_count"], 156)
        self.assertEqual(result["qdrant_total_count"], 156)
        for builder in (build_tables, build_columns, build_metrics):
            builder.assert_called_once_with(
                db,
                embedding_client=embedding_client,
                qdrant_repository=qdrant_repository,
            )
