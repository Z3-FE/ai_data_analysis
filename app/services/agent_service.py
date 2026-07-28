"""Agent 服务层。

负责创建初始 State、组装 AgentContext、执行 LangGraph，并把结果包装成接口层可用
的数据结构。路由层不直接接触 LangGraph 细节。
"""

import json
from collections.abc import AsyncIterator

from app.agent.context import AgentContext
from app.agent.graph import agent_graph
from app.agent.state import AgentState
from app.clients.embedding_client import EmbeddingClient
from app.clients.llm_client import LLMClient
from app.repositories.elasticsearch_repository import ElasticsearchRepository
from app.repositories.qdrant_repository import QdrantRepository


class AgentService:
    """封装当前问数 Agent 执行入口。"""

    def __init__(
        self,
        llm_client: LLMClient,
        embedding_client: EmbeddingClient,
        qdrant_repository: QdrantRepository,
        elasticsearch_repository: ElasticsearchRepository,
    ) -> None:
        self.llm_client = llm_client
        self.embedding_client = embedding_client
        self.qdrant_repository = qdrant_repository
        self.elasticsearch_repository = elasticsearch_repository

    def _context(self) -> AgentContext:
        """组装本次图执行使用的外部依赖。"""
        return AgentContext(
            llm_client=self.llm_client,
            embedding_client=self.embedding_client,
            qdrant_repository=self.qdrant_repository,
            elasticsearch_repository=self.elasticsearch_repository,
        )

    def _format_result(self, input_text: str, result: AgentState) -> dict:
        """把图执行结果整理成接口响应结构。"""
        return {
            "input_text": input_text,
            "original_question": result.get("original_question", input_text),
            "llm_keywords": result.get("llm_keywords", []),
            "jieba_keywords": result.get("jieba_keywords", []),
            "keywords": result.get("keywords", []),
            "column_recall_terms": result.get("column_recall_terms", []),
            "column_candidates": result.get("column_candidates", []),
            "output_text": result.get("output_text", ""),
            "llm_output": result.get("llm_output", ""),
        }

    def run(self, input_text: str) -> dict:
        """同步执行当前 Agent 图并返回结构化结果。"""
        state: AgentState = AgentState(input_text=input_text)
        result = agent_graph.invoke(input=state, context=self._context())
        return self._format_result(input_text, result)

    async def qyStream(self, input_text: str) -> AsyncIterator[str]:
        """以 SSE 文本形式流式返回当前 Agent 执行过程。"""
        state: AgentState = AgentState(input_text=input_text)
        try:
            async for chunk in agent_graph.astream(
                input=state,
                context=self._context(),
                stream_mode="custom",
            ):
                yield f"data: {json.dumps(chunk, ensure_ascii=False, default=str)}\n\n"
        except Exception as exc:
            error = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(error, ensure_ascii=False, default=str)}\n\n"
