"""Embedding 客户端管理器。

负责按配置初始化 Embedding 服务客户端，并为字段、指标和用户问题的向量化
提供统一访问入口。
"""

import asyncio
from typing import Optional

from langchain_huggingface import HuggingFaceEndpointEmbeddings

from app.core.config import EmbeddingConfig, settings


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
        self.client: Optional[HuggingFaceEndpointEmbeddings] = None
        self.config = config

    def _get_url(self) -> str:
        """拼接 Embedding 服务地址"""
        return f"http://{self.config.host}:{self.config.port}"

    def init(self):
        """显式初始化客户端，避免模块导入时立即建立外部连接"""
        self.client = TimeoutHuggingFaceEmbeddings(
            model=self._get_url(),
            timeout_seconds=self.config.timeout_seconds,
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
