"""AutoLLM 非流式超时和底层重试配置测试。"""

import asyncio
import unittest

from app.clients.auto_llm_client import AutoLLMAdapter, AutoLLMConfig


class AutoLlmClientTest(unittest.IsolatedAsyncioTestCase):
    """验证非流式调用不会被底层重试隐藏，并受统一超时控制。"""

    def _config(self, timeout_seconds: float = 0.02) -> AutoLLMConfig:
        return AutoLLMConfig(
            provider="mock",
            model_name="test-model",
            api_key="",
            base_url="",
            timeout_seconds=timeout_seconds,
        )

    async def test_ainvoke_times_out_with_explicit_message(self) -> None:
        class SlowModel:
            async def ainvoke(self, *_args, **_kwargs):
                await asyncio.sleep(0.05)

        adapter = AutoLLMAdapter(self._config(), model=SlowModel())

        with self.assertRaisesRegex(TimeoutError, "LLM 非流式响应超时"):
            await adapter.ainvoke("test")

    def test_openai_factory_disables_hidden_retries(self) -> None:
        from app.clients.auto_llm_client import AutoLLMModelFactory

        model = AutoLLMModelFactory.create(
            AutoLLMConfig(
                provider="openai-compatible",
                model_name="test-model",
                api_key="test-key",
                base_url="https://example.invalid/v1",
                timeout_seconds=30,
            )
        )

        self.assertEqual(model.max_retries, 0)


if __name__ == "__main__":
    unittest.main()
