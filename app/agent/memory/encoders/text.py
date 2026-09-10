"""复用项目现有 Embedding 服务的文本编码器。"""

import inspect
from typing import Any


class TextMemoryEncoder:
    """把 Embedding 客户端适配为记忆层的异步编码器。"""

    def __init__(
        self,
        client: Any,
        *,
        name: str = "configured-text-embedding",
        dimension: int | None = None,
    ) -> None:
        # 项目现有的 HuggingFaceEndpointEmbeddings 或兼容客户端。
        self.client = client
        # 用于索引元数据和模型切换后的重建判断。
        self.name = name
        # 配置声明的维度；首次编码后也会更新为实际维度。
        self.dimension = dimension

    async def encode(self, text: str) -> list[float]:
        """编码单条文本。"""
        if not text.strip():
            raise ValueError("记忆文本不能为空")
        method = getattr(self.client, "aembed_query", None)
        if method is None:
            method = getattr(self.client, "embed_query", None)
        if method is None:
            raise TypeError("Embedding 客户端缺少 aembed_query/embed_query 方法")
        value = method(text)
        if inspect.isawaitable(value):
            value = await value
        vector = [float(item) for item in value]
        self.dimension = len(vector)
        return vector
