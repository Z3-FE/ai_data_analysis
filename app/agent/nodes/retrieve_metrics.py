import logging
from dataclasses import asdict

from langchain_core.output_parsers import JsonOutputParser
from langgraph.runtime import Runtime
from langchain_core.prompts import PromptTemplate

from app.agent.context import AgentContext
from app.agent.prompts.prompt_loader import load_prompt
from app.agent.state import AgentState
from app.agent.utils.terms import build_recall_terms
from app.entities.qdrant import QdMetaMetrics

logger = logging.getLogger(__name__)

async def search_metrics_by_terms(recall_terms: list[str], runtime: Runtime) -> list[QdMetaMetrics]:
    metrics_semantic_map: dict[str, QdMetaMetrics] = {}
    embedding = runtime.context['embedding_client']
    meta_metrics_semantic_repository = runtime.context['meta_metrics_semantic_repository']
    for term in recall_terms:
        vortices = await embedding.aembed_query(term)
        hits:list[QdMetaMetrics] = await meta_metrics_semantic_repository.search(vector=vortices)
        for hit in hits:
            metric_id = hit.payload.metric_id
            if metric_id is None:
                continue

            if metrics_semantic_map.get(metric_id) is None or hit.score > metrics_semantic_map.get(metric_id).score:
                metrics_semantic_map[metric_id] = hit



    return sorted(metrics_semantic_map.values(), key=lambda h: h.score, reverse=True)

async def retrieve_metrics(state:AgentState, runtime:Runtime[AgentContext]):
    writer = runtime.stream_writer
    step = "召回metrics字段信息"
    writer({"type": "progress", "step": step, "status": "running"})

    question = state.get("input_text", "")
    keywords = state["keywords"]
    llm = runtime.context["llm_client"]
    prompt = PromptTemplate(template=load_prompt("extend_keywords_for_metric_recall"))
    chain = prompt | llm | JsonOutputParser()

    llm_keywords:list[str] = await chain.ainvoke({"query": question, "keywords": keywords})
    # 有序去重合并关键字
    recall_terms: list[str] = build_recall_terms(llm_keywords, keywords)
    # 召回表中相关字段
    metrics_semantic:list[QdMetaMetrics] = await search_metrics_by_terms(recall_terms, runtime)
    metrics_candidates = [asdict(semantic) for semantic in metrics_semantic]
    writer(
        {
            "type": "metrics",
            "step": step,
            "status": "success",
            "metrics_recall_terms": recall_terms,
            "metrics_candidates": metrics_candidates,
        }
    )
    logger.info("召回的metrics对应的字段数据: %s", metrics_semantic)
    return {
        "metrics_recall_terms":recall_terms,
        "metrics_candidates": metrics_candidates
    }
