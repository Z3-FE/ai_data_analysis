import logging
from dataclasses import asdict
from pprint import pprint

from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.state import AgentState
from app.agent.utils.terms import build_recall_terms

from app.entities.qdrant import QdMetaTables

logger = logging.getLogger(__name__)


async def search_tables_by_terms(recall_terms: list[str] , runtime: Runtime) -> list[QdMetaTables]:
    """召回功能"""
    tables_semantic_map: dict[str,QdMetaTables] = {}
    embedding_client = runtime.context["embedding_client"]
    repository = runtime.context["meta_tables_semantic_repository"]
    for recall_term in recall_terms:
        vector = await embedding_client.aembed_query(recall_term)

        hits: list[QdMetaTables] = await repository.search(vector=vector)
        for hit in hits:
            table_id = hit.payload.table_id
            if not table_id:
                continue

            existing = tables_semantic_map.get(table_id)
            if existing is None or hit.score > existing.score:
                tables_semantic_map[table_id] = hit

    return sorted(tables_semantic_map.values(), key=lambda x: x.score, reverse=True)

async def retrieve_tables(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> AgentState:
    """召回和用户问题相关的字段tables信息。"""
    writer = runtime.stream_writer
    step = "召回tables字段信息"
    writer({"type": "progress", "step": step, "node": "retrieve_tables", "status": "running"})

    question = state.get("input_text", "")
    recall_terms: list[str] = build_recall_terms(state.get("keywords", []), [question])

    table_semantics: list[QdMetaTables] = await search_tables_by_terms(recall_terms, runtime)
    # print(f"table_semantics{table_semantics}")
    table_candidates = [asdict(semantic) for semantic in table_semantics]

    writer(
        {
            "type": "tables",
            "step": step,
            "node": "retrieve_tables",
            "status": "success",
            "table_recall_terms": recall_terms,
            "table_candidates": table_candidates,
        }
    )
    logger.info("召回的table对应的字段数据: %s", table_candidates)
    return {
            "table_recall_terms": recall_terms,
        "table_candidates": table_candidates,
    }
