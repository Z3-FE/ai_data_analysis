"""Embedding 推理服务客户端。

当前通过 Text Embeddings Inference 服务把文本转换为向量。后续如果更换模型或
服务地址，只需要调整 `config.yaml` 和这个客户端。
"""

import logging
import time

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmbeddingClient:
    """调用 Embedding 服务的轻量客户端。"""

    def __init__(self, base_url: str | None = None, timeout: float = 120.0) -> None:
        self.base_url = (base_url or settings.embedding.url).rstrip("/")
        self.timeout = timeout

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量生成文本向量。"""
        if not texts:
            return []

        vectors: list[list[float]] = []
        batch_size = settings.embedding.batch_size
        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            vectors.extend(self._embed_batch(batch))
        return vectors

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        """生成单个小批次向量，遇到限流或临时连接异常时重试。"""
        last_error: Exception | None = None
        for attempt in range(settings.embedding.retry_count + 1):
            try:
                response = httpx.post(
                    f"{self.base_url}/embed",
                    json={"inputs": texts},
                    timeout=self.timeout,
                )
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = httpx.HTTPStatusError(
                        "Embedding 服务暂时不可用。",
                        request=response.request,
                        response=response,
                    )
                else:
                    response.raise_for_status()
                    vectors = response.json()
                    if not isinstance(vectors, list):
                        raise ValueError("Embedding 服务返回格式不是向量列表。")
                    return vectors
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc

            if attempt < settings.embedding.retry_count:
                wait_seconds = settings.embedding.retry_backoff_seconds * (attempt + 1)
                logger.warning(
                    "Embedding 请求失败，第 %s 次重试将在 %.1f 秒后执行：%s",
                    attempt + 1,
                    wait_seconds,
                    last_error,
                )
                time.sleep(wait_seconds)

        if last_error is not None:
            raise RuntimeError(
                f"Embedding 服务连续请求失败，共尝试 {settings.embedding.retry_count + 1} 次。"
            ) from last_error

        raise RuntimeError("Embedding 服务请求失败。")

    def health(self) -> bool:
        """检查 Embedding 服务是否可访问。"""
        try:
            response = httpx.get(f"{self.base_url}/health", timeout=5.0)
            return response.status_code == 200
        except httpx.HTTPError:
            return False
