"""不依赖向量服务的中英文 TF-IDF 与关键词相似度。"""

import math
import re
from collections import Counter

import jieba


def tokenize(text: str) -> list[str]:
    """把中英文文本切成可用于短消息检索的词项。"""
    normalized = text.strip().lower()
    if not normalized:
        return []
    tokens = [
        token.strip()
        for token in jieba.lcut_for_search(normalized)
        if token.strip() and re.search(r"[a-z0-9_\u4e00-\u9fff]", token)
    ]
    return tokens or re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", normalized)


def lexical_scores(query: str, documents: list[str]) -> list[float]:
    """融合 TF-IDF 余弦相似度、关键词覆盖和完整子串命中。"""
    if not documents:
        return []
    query_tokens = tokenize(query)
    document_tokens = [tokenize(document) for document in documents]
    if not query_tokens:
        # 空查询用于按附件 ID 等结构化条件精确读取，不伪造词法相关度。
        return [0.0] * len(documents)

    frequencies = Counter(
        token
        for tokens in [query_tokens, *document_tokens]
        for token in set(tokens)
    )
    document_count = len(documents) + 1

    def vector(tokens: list[str]) -> dict[str, float]:
        counts = Counter(tokens)
        total = max(1, len(tokens))
        return {
            token: (count / total)
            * (math.log((document_count + 1) / (frequencies[token] + 1)) + 1.0)
            for token, count in counts.items()
        }

    query_vector = vector(query_tokens)
    query_norm = math.sqrt(sum(value * value for value in query_vector.values()))
    query_terms = set(query_tokens)
    normalized_query = query.strip().lower()
    scores: list[float] = []
    for document, tokens in zip(documents, document_tokens, strict=True):
        document_vector = vector(tokens)
        document_norm = math.sqrt(
            sum(value * value for value in document_vector.values())
        )
        dot_product = sum(
            value * document_vector.get(token, 0.0)
            for token, value in query_vector.items()
        )
        cosine = (
            dot_product / (query_norm * document_norm)
            if query_norm and document_norm
            else 0.0
        )
        keyword_overlap = len(query_terms & set(tokens)) / max(1, len(query_terms))
        substring = 1.0 if normalized_query in document.lower() else 0.0
        scores.append(
            min(1.0, cosine * 0.65 + keyword_overlap * 0.25 + substring * 0.1)
        )
    return scores


__all__ = ["lexical_scores", "tokenize"]
