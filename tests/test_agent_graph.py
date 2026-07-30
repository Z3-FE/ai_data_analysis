"""最小 Agent 图测试。"""

import unittest

from types import SimpleNamespace

from langchain_core.runnables import RunnableLambda

from app.services.agent_service import AgentService


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


class AgentGraphTest(unittest.TestCase):
    """验证最小 LangGraph 能正常返回结果。"""

    def test_run_returns_echo_result(self) -> None:
        result = AgentService(
            llm_client=RunnableLambda(lambda prompt: "[]"),
            embedding_client=SimpleNamespace(aembed_query=lambda text: _fake_aembed_query(text)),
            qdrant_repository=FakeQdrantRepository(),
            elasticsearch_repository=object(),
            meta_tables_semantic_repository=FakeSemanticRepository(),
            meta_columns_semantic_repository=FakeSemanticRepository(),
            meta_metrics_semantic_repository=FakeSemanticRepository(),
            meta_dimension_values_semantic_repository=FakeSemanticRepository(),
        ).run("你好")

        self.assertEqual(result["input_text"], "你好")
        self.assertIn("keywords", result)
        self.assertIn("original_question", result)
        self.assertIn("llm_keywords", result)
        self.assertIn("jieba_keywords", result)
        self.assertIn("column_recall_terms", result)
        self.assertIn("column_candidates", result)
        self.assertIsInstance(result["keywords"], list)
        self.assertIsInstance(result["llm_keywords"], list)
        self.assertIsInstance(result["jieba_keywords"], list)
        self.assertIsInstance(result["column_recall_terms"], list)
        self.assertIsInstance(result["column_candidates"], list)
        self.assertTrue(result["output_text"])


if __name__ == "__main__":
    unittest.main()
