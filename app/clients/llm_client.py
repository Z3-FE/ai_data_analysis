"""LLM 客户端生命周期管理。

应用只在这里根据项目配置创建一个 AutoLLM 客户端。业务节点不直接创建
ChatOpenAI，也不直接读取任何模型服务商的原生客户端字段。
"""

from app.clients.auto_llm_client import AutoLLMAdapter, AutoLLMConfig
from app.core.config import LlmConfig, settings


class LLMClientManager:
    """管理项目 AutoLLM 客户端的初始化和关闭。"""

    def __init__(self, config: LlmConfig) -> None:
        self.config = config
        self.client: AutoLLMAdapter | None = None

    def init(self) -> None:
        """根据配置创建 AutoLLM 适配器。"""
        auto_config = AutoLLMConfig(
            provider=self.config.provider,  # type: ignore[arg-type]
            model_name=self.config.model_name,
            api_key=self.config.api_key,
            base_url=self.config.base_url,
            timeout_seconds=self.config.timeout_seconds,
            max_tokens=self.config.max_tokens,
            request_options=dict(self.config.request_options),
            include_usage=self.config.include_usage,
            stream_only=self.config.stream_only,
        )
        self.client = AutoLLMAdapter(auto_config)

    def close(self) -> None:
        """清理应用级客户端引用。"""
        self.client = None

    async def aclose(self) -> None:
        """关闭 AutoLLM 底层连接，再清理应用级引用。"""
        if self.client is not None:
            await self.client.aclose()
        self.close()


llm_client_manager = LLMClientManager(settings.llm)
