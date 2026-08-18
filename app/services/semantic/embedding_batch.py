"""Embedding 文档分批工具。"""

from collections.abc import Sequence


def embed_documents_in_batches(
    embedding_client,
    texts: Sequence[str],
    batch_size: int,
) -> list[list[float]]:
    """按服务允许的批量大小生成向量，并保持输入顺序。"""
    if batch_size <= 0:
        raise ValueError("Embedding batch_size 必须大于 0。")

    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        batch = list(texts[start : start + batch_size])
        vectors.extend(embedding_client.embed_documents(batch))
    return vectors
