"""字段召回节点。

第二块积木：根据关键词召回可能相关的字段元数据。
流程是：字段召回词扩展 -> Embedding -> Qdrant 检索 -> 按 column_id 去重。
"""

import json
import re
from pathlib import Path
from typing import Any

from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.state import AgentState
from app.core.config import settings

_PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "extend_keywords_for_column_recall.prompt"
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
    terms = []
    seen = set()
    for value in values:
        term = str(value).strip()
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
    return terms


def _load_prompt() -> str:
    """读取字段召回扩展提示词。"""
    return _PROMPT_PATH.read_text(encoding="utf-8")



def _build_column_candidate(hit: dict[str, Any], matched_term: str) -> dict[str, Any]:
    """把 Qdrant 命中结果转换成字段候选。"""
    payload = hit.get("payload", {})
    return {
        "column_id": payload.get("column_id", ""),
        "table_id": payload.get("table_id", ""),
        "column_name": payload.get("column_name", ""),
        "business_name": payload.get("business_name", ""),
        "data_type": payload.get("data_type", ""),
        "semantic_role": payload.get("semantic_role", ""),
        "description": payload.get("description", ""),
        "aliases": payload.get("aliases", []),
        "score": float(hit.get("score", 0.0)),
        "matched_term": matched_term,
        "matched_vector_type": payload.get("vector_type", ""),
        "text": payload.get("text", ""),
    }


def _merge_column_candidate(
    candidates: dict[str, dict[str, Any]],
    candidate: dict[str, Any],
) -> None:
    """按 column_id 去重，保留最高分并累计命中的召回词。"""
    column_id = candidate["column_id"]
    if not column_id:
        return

    existing = candidates.get(column_id)
    if existing is None:
        candidate["matched_terms"] = [candidate.pop("matched_term")]
        candidate["matched_vector_types"] = [candidate["matched_vector_type"]]
        candidates[column_id] = candidate
        return

    matched_term = candidate["matched_term"]
    if matched_term not in existing["matched_terms"]:
        existing["matched_terms"].append(matched_term)
    vector_type = candidate["matched_vector_type"]
    if vector_type and vector_type not in existing["matched_vector_types"]:
        existing["matched_vector_types"].append(vector_type)
    if candidate["score"] > existing["score"]:
        existing.update(
            {key: value for key, value in candidate.items() if key != "matched_term"}
        )


def retrieve_columns(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> AgentState:
    """召回和用户问题相关的字段元数据。"""
    writer = runtime.stream_writer
    step = "召回字段信息"
    writer({"type": "progress", "step": step, "status": "running"})

    original_question = state.get("original_question") or state.get("input_text", "")
    keywords = state.get("keywords", [])

    # 拓展关联字
    """使用 LLM 把用户问题扩展成字段召回词。"""
    prompt = _load_prompt().format(
        query=original_question,
        keywords=json.dumps(keywords, ensure_ascii=False),
    )
    llm_output = runtime.context["llm_client"].chat(prompt)
    expanded_terms =  _parse_json_array(llm_output)

    # 关键字去重
    recall_terms = _dedupe_terms([*keywords, *expanded_terms, original_question])

    # 召回 columns数据
    embedding_client = runtime.context["embedding_client"]
    qdrant_repository = runtime.context["qdrant_repository"]
    candidates: dict[str, dict[str, Any]] = {}
    for term in recall_terms:
        vector = embedding_client.embed_texts([term])[0]
        hits = qdrant_repository.search_points(
            collection_name=settings.qdrant.columns_collection,
            vector=vector,
            limit=5,
            score_threshold=0.55,
        )
        for hit in hits:
            candidate = _build_column_candidate(hit, matched_term=term)
            _merge_column_candidate(candidates, candidate)

    column_candidates = sorted(
        candidates.values(),
        key=lambda item: item["score"],
        reverse=True,
    )
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
