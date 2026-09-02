"""Data Agent 记忆层基础契约测试。"""

import unittest

from app.agent.memory import (
    MemoryItem,
    MemoryMatch,
    MemoryReadRequest,
    MemoryScope,
    MemorySource,
    MemoryStatus,
    MemoryType,
)


class AgentMemoryModelsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.scope = MemoryScope(
            user_id=" user-1 ",
            agent_id="data-agent",
            conversation_id="conversation-1",
        )
        self.source = MemorySource(
            source_type="conversation_message",
            source_id="message-1",
            turn_id="turn-1",
            message_id="message-1",
        )

    def test_memory_item_serializes_scope_source_and_neutral_scores(self) -> None:
        item = MemoryItem(
            memory_type=MemoryType.SEMANTIC,
            content=" 用户偏好使用中文回答。 ",
            scope=self.scope,
            source=self.source,
        )

        payload = item.to_dict()

        self.assertEqual(item.content, "用户偏好使用中文回答。")
        self.assertEqual(item.importance, 0.5)
        self.assertEqual(item.confidence, 0.5)
        self.assertEqual(payload["memory_type"], "semantic")
        self.assertEqual(payload["status"], "active")
        self.assertEqual(
            payload["scope"],
            {
                "user_id": "user-1",
                "agent_id": "data-agent",
                "conversation_id": "conversation-1",
            },
        )
        self.assertEqual(payload["source"]["turn_id"], "turn-1")

    def test_all_four_memory_types_are_available_by_default(self) -> None:
        request = MemoryReadRequest(scope=self.scope)

        self.assertEqual(request.memory_types, frozenset(MemoryType))
        self.assertEqual(
            {memory_type.value for memory_type in request.memory_types},
            {"working", "episodic", "semantic", "perceptual"},
        )

    def test_read_request_normalizes_types_and_asset_ids(self) -> None:
        request = MemoryReadRequest(
            scope=self.scope,
            query=" 这张图里的趋势是什么？ ",
            memory_types=frozenset({"working", "perceptual"}),
            asset_ids=(" asset-1 ", "", "asset-2"),
            limit=5,
        )

        self.assertEqual(request.query, "这张图里的趋势是什么？")
        self.assertEqual(
            request.memory_types,
            frozenset({MemoryType.WORKING, MemoryType.PERCEPTUAL}),
        )
        self.assertEqual(request.asset_ids, ("asset-1", "asset-2"))

    def test_exact_conversation_read_requires_conversation_id(self) -> None:
        with self.assertRaisesRegex(ValueError, "必须提供 conversation_id"):
            MemoryReadRequest(
                scope=MemoryScope(user_id="user-1"),
                memory_types={MemoryType.WORKING},
                exact_conversation=True,
            )

        request = MemoryReadRequest(
            scope=self.scope,
            memory_types={MemoryType.WORKING},
            exact_conversation=True,
        )
        self.assertTrue(request.exact_conversation)

    def test_scope_requires_user_and_omits_unset_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "user_id 不能为空"):
            MemoryScope(user_id=" ")

        self.assertNotIn("tenant_id", self.scope.to_dict())
        self.assertNotIn("project_id", self.scope.to_dict())

    def test_invalid_score_and_validity_range_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "importance 必须在 0 到 1 之间"):
            MemoryItem(
                memory_type=MemoryType.EPISODIC,
                content="一次历史分析经历。",
                scope=self.scope,
                source=self.source,
                importance=1.1,
            )

        item = MemoryItem(
            memory_type=MemoryType.WORKING,
            content="当前活动目标。",
            scope=self.scope,
            source=self.source,
        )
        with self.assertRaisesRegex(ValueError, "score 必须在 0 到 1 之间"):
            MemoryMatch(item=item, score=-0.1)

    def test_status_accepts_storage_string_values(self) -> None:
        item = MemoryItem(
            memory_type="perceptual",
            content="图片中包含一张销售趋势折线图。",
            scope=self.scope,
            source=self.source,
            status="archived",
        )

        self.assertEqual(item.memory_type, MemoryType.PERCEPTUAL)
        self.assertEqual(item.status, MemoryStatus.ARCHIVED)


if __name__ == "__main__":
    unittest.main()
