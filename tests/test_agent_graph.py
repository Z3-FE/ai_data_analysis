"""最小 Agent 图测试。"""

import unittest

from app.clients.embedding_client import EmbeddingClient
from app.clients.llm_client import LLMClient
from app.services.agent_service import AgentService


class FakeQdrantRepository:
    """提供 retrieve_columns 所需的最小仓库接口。"""

    def search_points(self, **kwargs) -> list[dict]:
        return []


class AgentGraphTest(unittest.TestCase):
    """验证最小 LangGraph 能正常返回结果。"""

    def test_run_returns_echo_result(self) -> None:
        result = AgentService(
            llm_client=LLMClient(),
            embedding_client=EmbeddingClient(),
            qdrant_repository=FakeQdrantRepository(),
            elasticsearch_repository=object(),
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
        self.assertTrue(result["llm_output"])


if __name__ == "__main__":
    unittest.main()
