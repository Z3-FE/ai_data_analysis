"""最小 Agent 图测试。"""

import unittest

from types import SimpleNamespace

from langchain_core.runnables import RunnableLambda

from app.services.agent_service import AgentService


class FakeDwRepository:
    """提供 SQL 执行节点所需的最小仓库接口。"""

    async def execute_query(self, sql: str) -> list[dict]:
        return []


async def _fake_aembed_query(text: str) -> list[float]:
    """返回固定查询向量。"""
    return [0.1] * 1024


class FakeQdrantRepository:
    """提供通用 Qdrant 仓库的最小接口。"""

    def search_points(self, **kwargs) -> list[dict]:
        return []


class FakeSemanticRepository:
    """提供语义集合检索所需的最小接口。"""

    async def search(self, **kwargs) -> list:
        return []


class FakeElasticsearchRepository:
    """提供维度值 ES 异步检索所需的最小接口。"""

    async def search(self, **kwargs) -> list[dict]:
        return []


class FakeMetaCatalogRepository:
    """提供召回合并节点所需的空 Meta 查询结果。"""

    async def get_columns_by_ids(self, column_ids):
        return []

    async def get_tables_by_ids(self, table_ids):
        return []

    async def get_queryable_columns_by_table_ids(self, table_ids):
        return []

    async def get_relationships_by_table_ids(self, table_ids):
        return []

    async def get_dimensions_by_column_ids(self, column_ids):
        return []

    async def get_metric_dimension_infos(self, metric_ids, dimension_ids):
        return []


class AgentGraphTest(unittest.TestCase):
    """验证最小 LangGraph 能正常返回结果。"""

    def test_run_returns_echo_result(self) -> None:
        result = AgentService(
            llm_client=RunnableLambda(lambda prompt: "[]"),
            embedding_client=SimpleNamespace(aembed_query=lambda text: _fake_aembed_query(text)),
            dimension_value_search=FakeElasticsearchRepository(),
            meta_tables_semantic_repository=FakeSemanticRepository(),
            meta_columns_semantic_repository=FakeSemanticRepository(),
            meta_metrics_semantic_repository=FakeSemanticRepository(),
            meta_dimension_values_semantic_repository=FakeSemanticRepository(),
            meta_catalog_repository=FakeMetaCatalogRepository(),
            dw_repository=FakeDwRepository(),
        ).run("你好")

        self.assertEqual(result["input_text"], "你好")
        self.assertIn("keywords", result)
        self.assertIn("original_question", result)
        self.assertIn("llm_keywords", result)
        self.assertIn("jieba_keywords", result)
        self.assertIn("column_recall_terms", result)
        self.assertIn("column_candidates", result)
        self.assertIn("dimension_value_recall_terms", result)
        self.assertIn("dimension_value_candidates", result)
        self.assertIn("table_infos", result)
        self.assertIn("metric_infos", result)
        self.assertIn("dimension_infos", result)
        self.assertIn("relationship_infos", result)
        self.assertIn("metric_dimension_infos", result)
        self.assertIsInstance(result["keywords"], list)
        self.assertIsInstance(result["llm_keywords"], list)
        self.assertIsInstance(result["jieba_keywords"], list)
        self.assertIsInstance(result["column_recall_terms"], list)
        self.assertIsInstance(result["column_candidates"], list)
        self.assertIsInstance(result["dimension_value_recall_terms"], list)
        self.assertIsInstance(result["dimension_value_candidates"], list)
        self.assertTrue(result["output_text"])


if __name__ == "__main__":
    unittest.main()
