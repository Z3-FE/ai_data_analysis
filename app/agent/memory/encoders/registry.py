"""按模态管理记忆编码器。"""

from app.agent.memory.encoders.base import MemoryEncoder


class EncoderRegistry:
    """记忆模态和编码器之间的可替换注册表。"""

    def __init__(self) -> None:
        # 当前只注册 text；image/audio/video 的扩展点已经固定。
        self._encoders: dict[str, MemoryEncoder] = {}

    def register(self, modality: str, encoder: MemoryEncoder) -> None:
        """注册一个模态编码器。"""
        self._encoders[modality] = encoder

    def get(self, modality: str) -> MemoryEncoder | None:
        """返回模态编码器；没有实现的模态返回 None。"""
        return self._encoders.get(modality)

    def require(self, modality: str) -> MemoryEncoder:
        """返回模态编码器，否则抛出清晰错误。"""
        encoder = self.get(modality)
        if encoder is None:
            raise RuntimeError(f"尚未配置 {modality} 模态的记忆编码器")
        return encoder
