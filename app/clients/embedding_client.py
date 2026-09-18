"""Embedding 客户端管理器。

负责按配置初始化 Embedding 服务客户端，并为字段、指标和用户问题的向量化
提供统一访问入口。
"""

import asyncio
import logging
from typing import Optional

from langchain_core.embeddings import Embeddings
from langchain_huggingface import HuggingFaceEndpointEmbeddings
from langchain_openai import OpenAIEmbeddings

from app.core.config import EmbeddingConfig, settings

logger = logging.getLogger(__name__)


class TimeoutHuggingFaceEmbeddings(HuggingFaceEndpointEmbeddings):
    """给 embedding 调用加硬超时；本地推理服务挂起时快速失败而不是无限等待。"""

    timeout_seconds: float = 30.0

    async def aembed_query(self, text: str) -> list[float]:
        return await asyncio.wait_for(
            super().aembed_query(text), timeout=self.timeout_seconds
        )

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.wait_for(
            super().aembed_documents(texts), timeout=self.timeout_seconds
        )


class EmbeddingClientManager:
    """管理 Embedding 服务客户端的初始化与复用"""

    def __init__(self, config: EmbeddingConfig):
        self.client: Optional[Embeddings] = None
        self.config = config

    def _build_client(self) -> Embeddings:
        """按 provider 构建对应的 Embedding 客户端。"""
        if self.config.provider == "openai-compatible":
            # check_embedding_ctx_length=False 避免按 OpenAI 词表切分中文文本。
            return OpenAIEmbeddings(
                model=self.config.model_name,
                dimensions=self.config.dimensions,
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                request_timeout=self.config.timeout_seconds,
                max_retries=0,
                chunk_size=self.config.batch_size,
                check_embedding_ctx_length=False,
            )
        return TimeoutHuggingFaceEmbeddings(
            model=f"http://{self.config.host}:{self.config.port}",
            timeout_seconds=self.config.timeout_seconds,
        )

    def init(self):
        """显式初始化客户端，避免模块导入时立即建立外部连接"""
        self.client = self._build_client()
        logger.info(
            "Embedding 客户端已初始化：provider=%s model=%s dimensions=%s",
            self.config.provider,
            self.config.active_model,
            self.config.dimensions,
        )

    async def verify_dimension(self, expected: int) -> None:
        """启动期自检：真实调用一次 embedding，校验返回向量维度与配置一致。

        切换 embedding 模型意味着向量空间整体变化，必须重建 Qdrant
        collection；这里在启动时提前失败比运行期写入报错更容易定位。
        """
        vector = await self.client.aembed_query("维度自检")
        if len(vector) != expected:
            raise RuntimeError(
                f"Embedding 返回维度 {len(vector)} 与配置维度 {expected} 不一致，"
                "请检查 embedding.provider/model_name 与 qdrant.vector_size 是否匹配。"
            )


# 模块级单例，供整个项目复用同一套 Embedding 客户端管理器
embedding_client_manager = EmbeddingClientManager(settings.embedding)


if __name__ == "__main__":
    embedding_client_manager.init()
    client = embedding_client_manager.client

    async def test():
        """执行一次最小化向量化调用，验证服务是否可用"""
        text = "What is deep learning?"
        query_result = await client.aembed_query(text) 
        print(query_result[:3])

    asyncio.run(test())
