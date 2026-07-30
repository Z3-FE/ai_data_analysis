"""Embedding 客户端管理器。

负责按配置初始化 Embedding 服务客户端，并为字段、指标和用户问题的向量化
提供统一访问入口。
"""

from typing import Optional

from langchain_huggingface import HuggingFaceEndpointEmbeddings

from app.core.config import EmbeddingConfig, settings


class EmbeddingClientManager:
    """管理 Embedding 服务客户端的初始化与复用。"""

    def __init__(self, config: EmbeddingConfig) -> None:
        self.config = config
        self.client: Optional[HuggingFaceEndpointEmbeddings] = None

    def init(self) -> None:
        """显式初始化客户端，避免模块导入时立即建立外部连接。"""
        self.client = HuggingFaceEndpointEmbeddings(model=self.config.url)


embedding_client_manager = EmbeddingClientManager(settings.embedding)
