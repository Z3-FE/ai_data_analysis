"""LLM 客户端生命周期管理。

按教程风格集中初始化 LangChain Chat Model，节点通过 `invoke` 直接使用插件对象。
"""

from typing import Optional

from langchain.chat_models import init_chat_model

from app.core.config import LlmConfig, settings


class LLMClientManager:
    """管理 LLM 插件对象的初始化。"""

    def __init__(self, config: LlmConfig) -> None:
        self.config = config
        self.client = None

    def init(self) -> None:
        """显式初始化 LLM 插件对象。"""
        self.client = init_chat_model(
            model=self.config.model_name,
            model_provider="openai",
            base_url=self.config.base_url,
            api_key=self.config.api_key,
            temperature=0,
        )

    def close(self) -> None:
        """当前 LLM 插件对象无显式关闭逻辑，保留生命周期接口。"""
        self.client = None


llm_client_manager = LLMClientManager(settings.llm)
