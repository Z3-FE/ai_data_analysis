"""维度真实值的 ES + Qdrant 两级混合召回节点。"""

import asyncio
import logging
from dataclasses import asdict
from pprint import pprint
from typing import Any

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.prompts.prompt_loader import load_prompt
from app.agent.state import AgentState
from app.agent.utils.terms import build_recall_terms
from app.core.config import settings
from app.entities.es.es_dimension_value import EsDimensionValueHit
from app.entities.qdrant import QdMetaDimensionValue
from app.entities.agent.agent_retrieval import DimensionValueRetrievalCandidate
from app.services.semantic.dimension_value_search_service import (
    merge_all_term_results,
    merge_term_results,
)

logger = logging.getLogger(__name__)


async def _search_es_by_term(
    recall_term: str,
    runtime: Runtime[AgentContext],
) -> list[dict[str, Any]]:
    """使用一个召回词查询 ES，返回文本匹配候选。"""
    repository = runtime.context["dimension_value_search"]
    hits: list[EsDimensionValueHit] = await repository.search(
        query_text=recall_term,
        index_name=settings.elasticsearch.dimension_values_alias,
        limit=settings.dimension_value_search.es_top_k_per_term,
    )
    return [
        {
            "score": hit.score,
            "matched_queries": hit.matched_queries,
            "source": asdict(hit.document),
        }
        for hit in hits
    ]


async def _search_qdrant_by_term(
    recall_term: str,
    runtime: Runtime[AgentContext],
) -> list[dict[str, Any]]:
    """使用一个召回词查询 Qdrant，返回语义相似候选。"""
    embedding = runtime.context["embedding_client"]
    repository = runtime.context["meta_dimension_values_semantic_repository"]

    # Qdrant 只能用向量查询，所以先把当前关键词转换成 embedding。
    vector = await embedding.aembed_query(recall_term)
    hits: list[QdMetaDimensionValue] = await repository.search(
        vector=vector,
        score_threshold=settings.dimension_value_search.vector_score_threshold,
        limit=settings.dimension_value_search.vector_top_k_per_term,
    )
    return [
        {
            "id": hit.id,
            "score": hit.score,
            "payload": asdict(hit.payload),
        }
        for hit in hits
    ]


async def _retrieve_one_term(
    recall_term: str,
    runtime: Runtime[AgentContext],
) -> list[DimensionValueRetrievalCandidate]:
    """完成一个关键词的 ES、Qdrant 召回和第一次融合。"""
    # 同一个关键词的两条检索链路互不依赖，可以并行执行以减少等待时间。
    es_hits, vector_hits = await asyncio.gather(
        _search_es_by_term(recall_term, runtime),
        _search_qdrant_by_term(recall_term, runtime),
    )

    # print(f"####################recall_term:{recall_term}/n")
    # print(f"####################es_hits:{es_hits}/n")
    # print(f"####################vector_hits:{vector_hits}/n")

    # 当前关键词查到多少就参与融合；term_max_k 只是最大保留数量。
    candidates = merge_term_results(
        recall_term=recall_term,
        es_hits=es_hits,
        vector_hits=vector_hits,
        limit=settings.dimension_value_search.term_max_k,
        rrf_k=settings.dimension_value_search.rrf_k,
    )
    logger.info(
        "维度值单词召回完成 term=%s es=%s qdrant=%s merged=%s",
        recall_term,
        len(es_hits),
        len(vector_hits),
        len(candidates),
    )
    return candidates


async def retrieve_dimension_values(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> AgentState:
    """逐个召回关键词，再汇总为最终维度真实值候选。"""
    writer = runtime.stream_writer
    step = "召回dimension_values字段信息"
    writer({"type": "progress", "step": step, "status": "running"})

    # 步骤 1：使用 LLM 扩展适合维度真实值检索的表达。
    question = state.get("input_text", "")
    keywords = state["keywords"]
    llm = runtime.context["llm_client"]
    prompt = PromptTemplate(
        template=load_prompt("extend_keywords_for_dimension_value_recall")
    )
    chain = prompt | llm | JsonOutputParser()
    llm_keywords: list[str] = await chain.ainvoke(
        {"query": question, "keywords": keywords}
    )
    recall_terms: list[str] = build_recall_terms(llm_keywords, keywords)

    # 步骤 2：逐个处理 recall_term。
    # 无论某个词最后能否命中，它都会完整执行一次 ES + Qdrant 召回。
    # 两级融合尚未结束，这里传递候选对象，不提前转换为 Agent State 字典。
    term_results: list[list[DimensionValueRetrievalCandidate]] = []
    for recall_term in recall_terms:
        candidates = await _retrieve_one_term(recall_term, runtime)
        term_results.append(candidates)

    # 步骤 3：把所有关键词的候选做第二次融合。
    # 相同 (column_id, raw_value) 合并为一条，并汇总 matched_terms。
    dimension_value_candidates = merge_all_term_results(
        term_results=term_results,
        total_limit=settings.dimension_value_search.total_max_k,
        rrf_k=settings.dimension_value_search.rrf_k,
    )

    # 步骤 4：通过 SSE 和 State 输出结果，供后续 LangGraph 节点继续使用。
    writer(
        {
            "type": "dimension_values",
            "step": step,
            "status": "success",
            "dimension_value_recall_terms": recall_terms,
            "dimension_value_candidates": dimension_value_candidates,
        }
    )
    logger.info(
        "维度值全局召回完成 terms=%s candidates=%s",
        len(recall_terms),
        len(dimension_value_candidates),
    )
    return {
        "dimension_value_recall_terms": recall_terms,
        "dimension_value_candidates": dimension_value_candidates,
    }
