"""独立日常聊天服务测试。"""

import unittest
from datetime import datetime

from app.clients.auto_llm_client import AutoLLMResponse
from app.context_engine import ContextEngine
from app.services.daily_chat_service import DailyChatService


class FakeConversationRepository:
    """记录日常聊天服务使用的会话仓储调用。"""

    def __init__(self) -> None:
        self.messages = [
            {
                "message_id": "message-1",
                "turn_id": "turn-1",
                "role": "user",
                "message_type": "text",
                "content": "你好",
                "created_at": datetime(2026, 8, 30, 10, 0),
            },
            {
                "message_id": "message-2",
                "turn_id": "turn-1",
                "role": "assistant",
                "message_type": "text",
                "content": "你好，有什么可以帮你？",
                "created_at": datetime(2026, 8, 30, 10, 0, 1),
            },
        ]
        self.started: dict = {}
        self.finished: dict = {}

    async def get_recent_messages(self, **kwargs):
        return self.messages

    async def start_turn(self, **kwargs) -> None:
        self.started = kwargs

    async def finish_turn(self, **kwargs) -> None:
        self.finished = kwargs


class FakeLLMClient:
    """捕获最终模型消息并返回固定回答。"""

    def __init__(self) -> None:
        self.messages = []

    async def ainvoke_auto(self, messages):
        self.messages = messages
        return AutoLLMResponse(
            content="我记得我们刚才打过招呼。",
            metadata={"provider": "mock", "model_name": "test-model"},
        )


class DailyChatServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_compiles_history_calls_llm_and_saves_context_trace(self) -> None:
        repository = FakeConversationRepository()
        llm = FakeLLMClient()
        service = DailyChatService(
            llm_client=llm,
            conversation_repository=repository,
            context_engine=ContextEngine(token_counter=len),
            history_limit=10,
            context_token_budget=500,
        )

        result = await service.chat(
            input_text="你还记得刚才吗？",
            conversation_id="conversation-1",
            user_id="user-1",
        )

        self.assertEqual(result["execution_mode"], "daily_chat")
        self.assertEqual(result["content"], "我记得我们刚才打过招呼。")
        self.assertEqual(repository.started["conversation_id"], "conversation-1")
        self.assertEqual(repository.finished["status"], "completed")
        self.assertEqual(repository.finished["output_type"], "text")
        self.assertIn("context_trace", repository.finished)
        self.assertEqual(repository.finished["context_trace"]["history_count"], 2)
        self.assertEqual(
            llm.messages[-1],
            {"role": "user", "content": "你还记得刚才吗？"},
        )
        self.assertIn("你好", llm.messages[1]["content"])
        self.assertIn("不调用 SQL、Python 或数据", llm.messages[0]["content"])
        self.assertIn("分析流程", llm.messages[0]["content"])
        self.assertEqual(result["context"]["history_count"], 2)

    async def test_rejects_blank_question_before_reading_history(self) -> None:
        repository = FakeConversationRepository()
        service = DailyChatService(
            llm_client=FakeLLMClient(),
            conversation_repository=repository,
        )

        with self.assertRaisesRegex(ValueError, "input_text 不能为空"):
            await service.chat("  ", "conversation-1", "user-1")

        self.assertEqual(repository.started, {})

    async def test_does_not_include_current_question_in_history_context(self) -> None:
        repository = FakeConversationRepository()
        llm = FakeLLMClient()
        service = DailyChatService(
            llm_client=llm,
            conversation_repository=repository,
            context_engine=ContextEngine(token_counter=len),
        )

        await service.chat(
            input_text="这句话不应该被当成历史上下文",
            conversation_id="conversation-1",
            user_id="user-1",
        )

        context_message = llm.messages[1]["content"]
        self.assertNotIn("这句话不应该被当成历史上下文", context_message)
        self.assertIn("这句话不应该被当成历史上下文", llm.messages[-1]["content"])


if __name__ == "__main__":
    unittest.main()
