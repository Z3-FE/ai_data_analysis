"""字段召回节点。

第二块积木：根据关键词召回可能相关的字段元数据。
流程是：字段召回词扩展 -> Embedding -> Qdrant 检索 -> 按 column_id 去重。
"""

import json
import logging
import re
from dataclasses import asdict

from langchain_core.output_parsers import JsonOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.prompts.prompt_loader import load_prompt
from app.agent.state import AgentState
from app.entities.qdrant.meta_columns_semantic import MetaColumnsSemantic

logger = logging.getLogger(__name__)

_JSON_ARRAY_PATTERN = re.compile(r"\[[\s\S]*\]")


def _parse_json_array(raw_text: str) -> list[str]:
    """从模型输出中解析 JSON 数组。"""
    text = raw_text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_ARRAY_PATTERN.search(text)
        if not match:
            return []
        data = json.loads(match.group(0))

    if not isinstance(data, list):
        return []
    return _dedupe_terms(str(item) for item in data)


def _dedupe_terms(values) -> list[str]:
    """清洗召回词并保持顺序去重。"""
    terms: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = str(value).strip()
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def _build_recall_terms(
    keywords: list[str],
    expanded_terms: list[str],
    question: str,
) -> list[str]:
    """合并关键词、扩展词和原始问题，作为字段召回输入。"""
    return _dedupe_terms([*keywords, *expanded_terms, question])


def _merge_column_semantic(
    columns_map: dict[str, MetaColumnsSemantic],
    semantic: MetaColumnsSemantic,
) -> None:
    """按 column_id 去重，同一字段保留最高分命中。"""
    column_id = semantic.payload.column_id
    if not column_id:
        return

    existing = columns_map.get(column_id)
    if existing is None or semantic.score > existing.score:
        columns_map[column_id] = semantic


async def _search_columns_by_terms(
    recall_terms: list[str],
    runtime: Runtime[AgentContext],
) -> list[MetaColumnsSemantic]:
    """根据多个召回词检索字段语义集合，并按字段去重。"""
    columns_map: dict[str, MetaColumnsSemantic] = {}
    embedding_client = runtime.context["embedding_client"]
    repository = runtime.context["meta_columns_semantic_repository"]

    for term in recall_terms:
        vector = await embedding_client.aembed_query(term)
        hits = await repository.search(vector=vector)
        logger.info("召回语义：%s", term)
        logger.info("召回字段数量：%s", len(hits))
        for hit in hits:
            _merge_column_semantic(columns_map, hit)

    return sorted(columns_map.values(), key=lambda item: item.score, reverse=True)


async def retrieve_columns(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> AgentState:
    """召回和用户问题相关的字段元数据。"""
    writer = runtime.stream_writer
    step = "召回字段信息"
    writer({"type": "progress", "step": step, "status": "running"})

    question = state.get("input_text", "")
    keywords = state["keywords"]

    prompt = PromptTemplate(template=load_prompt("extend_keywords_for_column_recall"))
    chain = prompt | runtime.context["llm_client"] | JsonOutputParser()
    expanded_words = await chain.ainvoke({"query": question, "keywords": keywords})
    logger.info("拓展字段信息：%s", expanded_words)

    recall_terms = _build_recall_terms(keywords, expanded_words, question)
    column_semantics = await _search_columns_by_terms(recall_terms, runtime)
    column_candidates = [asdict(semantic) for semantic in column_semantics]

    writer(
        {
            "type": "columns",
            "step": step,
            "status": "success",
            "column_recall_terms": recall_terms,
            "column_candidates": column_candidates,
        }
    )
    return {
        "column_recall_terms": recall_terms,
        "column_candidates": column_candidates,
    }
