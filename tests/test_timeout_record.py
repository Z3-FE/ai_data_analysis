"""统一 LLM 超时记录测试。"""

import unittest

from app.agent.utils.timeout_record import classify_timeout, record_llm_timeout


class TimeoutRecordTest(unittest.TestCase):
    """验证超时记录可定位调用位置且不包含敏感配置。"""

    def test_classifies_stream_and_invoke_timeout(self) -> None:
        self.assertEqual(
            classify_timeout(TimeoutError("SQL 生成超时（连续 1800 秒没有返回）。")),
            ("stream", "stream_idle"),
        )
        self.assertEqual(
            classify_timeout(TimeoutError("LLM 流式请求超时（连续 1800 秒没有返回）。")),
            ("stream", "stream_idle"),
        )
        self.assertEqual(
            classify_timeout(TimeoutError("LLM 非流式响应超时（连续 1800 秒没有返回）。")),
            ("invoke", "non_stream_idle"),
        )
        self.assertEqual(
            classify_timeout(TimeoutError("Python 计算超时")),
            ("unknown", "unknown"),
        )

    def test_record_contains_diagnostic_context_only(self) -> None:
        events: list[dict] = []
        client = type(
            "Client",
            (),
            {
                "adapter_config": type(
                    "Config",
                    (),
                    {
                        "provider": "openai-compatible",
                        "model_name": "test-model",
                    },
                )(),
            },
        )()

        record = record_llm_timeout(
            node="execute_analysis",
            step="执行分析任务：category_sales",
            task_id="category_sales",
            phase="生成 Python 分析代码",
            call_mode="invoke",
            timeout_kind="non_stream_idle",
            timeout_seconds=1800,
            error="LLM 非流式响应超时（连续 1800 秒没有返回）。",
            llm_client=client,
            writer=events.append,
        )

        self.assertEqual(record["event"], "llm_timeout")
        self.assertEqual(record["node"], "execute_analysis")
        self.assertEqual(record["task_id"], "category_sales")
        self.assertEqual(record["phase"], "生成 Python 分析代码")
        self.assertEqual(record["timeout_seconds"], 1800)
        self.assertEqual(record["provider"], "openai-compatible")
        self.assertEqual(events[0]["type"], "timeout")
        self.assertEqual(events[0]["node"], "execute_analysis")
        self.assertEqual(events[0]["step"], "执行分析任务：category_sales")
        self.assertNotIn("api_key", record)
        self.assertNotIn("prompt", record)


if __name__ == "__main__":
    unittest.main()
