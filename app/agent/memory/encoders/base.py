"""记忆向量编码器端口。"""

from typing import Protocol


class MemoryEncoder(Protocol):
    """把记忆正文转换为向量的最小接口。"""

    # 编码器名称，用于记录向量索引的模型版本。
    name: str
    # 编码器输出维度；首次编码前可以为 None。
    dimension: int | None

    async def encode(self, text: str) -> list[float]: ...
