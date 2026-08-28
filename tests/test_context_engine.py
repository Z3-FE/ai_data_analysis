"""通用 ContextEngine 的核心行为测试。"""

import unittest

from app.context_engine import ContextEngine, ContextItem, ContextPolicy, ContextRequest


class ContextEngineTest(unittest.TestCase):
    def test_compile_isolates_scope_and_preserves_refs(self) -> None:
        engine = ContextEngine(token_counter=len)
        request = ContextRequest(
            current_input="11 月销售额",
            user_id="user-1",
            conversation_id="conversation-1",
            token_budget=200,
        )
        items = [
            ContextItem(
                item_id="same-conversation",
                content="上一轮分析了 12 月销售额。",
                source_type="analysis_output",
                source_ref="turn_outputs/turn-1",
                scope={
                    "user_id": "user-1",
                    "conversation_id": "conversation-1",
                },
                section="evidence",
            ),
            ContextItem(
                item_id="other-conversation",
                content="其他会话的销售额结果。",
                source_type="analysis_output",
                source_ref="turn_outputs/turn-2",
                scope={
                    "user_id": "user-1",
                    "conversation_id": "conversation-2",
                },
                section="evidence",
            ),
        ]

        compiled = engine.compile(request, items)

        self.assertEqual(
            [item.item_id for item in compiled.selected_items],
            ["same-conversation"],
        )
        self.assertEqual(compiled.source_refs, ["turn_outputs/turn-1"])
        self.assertEqual(compiled.trace.candidate_count, 2)
        self.assertEqual(compiled.trace.isolated_count, 1)
        self.assertEqual(
            compiled.dropped_items,
            [
                {
                    "item_id": "other-conversation",
                    "source_ref": "turn_outputs/turn-2",
                    "reason": "scope_mismatch",
                }
            ],
        )

    def test_compile_selects_relevant_item_first(self) -> None:
        engine = ContextEngine(token_counter=len)
        request = ContextRequest(current_input="销售额", token_budget=5)
        items = [
            ContextItem(
                item_id="relevant",
                content="销售额",
                source_type="history",
                section="history",
            ),
            ContextItem(
                item_id="irrelevant",
                content="天气情况",
                source_type="history",
                section="history",
            ),
        ]

        compiled = engine.compile(
            request,
            items,
            policy=ContextPolicy(
                relevance_weight=1.0,
                recency_weight=0.0,
                importance_weight=0.0,
            ),
        )

        self.assertEqual(
            [item.item_id for item in compiled.selected_items], ["relevant"]
        )
        self.assertTrue(
            any(
                item["item_id"] == "irrelevant" and item["reason"] == "token_budget"
                for item in compiled.dropped_items
            )
        )

    def test_protected_items_share_compression_budget(self) -> None:
        engine = ContextEngine(token_counter=len)
        request = ContextRequest(current_input="继续", token_budget=10)
        items = [
            ContextItem(
                item_id=f"protected-{index}",
                content=f"第{index}条需要始终保留的较长上下文",
                source_type="required_state",
                source_ref=f"state/{index}",
                section="state",
            )
            for index in (1, 2)
        ]

        compiled = engine.compile(
            request,
            items,
            policy=ContextPolicy(
                protected_source_types=frozenset({"required_state"}),
            ),
        )

        self.assertTrue(compiled.compression_applied)
        self.assertEqual(
            [item.item_id for item in compiled.selected_items],
            ["protected-1", "protected-2"],
        )
        self.assertLessEqual(compiled.trace.token_used, request.token_budget)
        self.assertEqual(compiled.source_refs, ["state/1", "state/2"])

    def test_duplicate_item_id_is_traced_and_first_item_wins(self) -> None:
        engine = ContextEngine(token_counter=len)
        request = ContextRequest(current_input="继续", token_budget=100)

        compiled = engine.compile(
            request,
            [
                ContextItem(
                    item_id="same-id",
                    content="第一条",
                    source_type="history",
                    source_ref="messages/1",
                ),
                ContextItem(
                    item_id="same-id",
                    content="第二条",
                    source_type="history",
                    source_ref="messages/2",
                ),
            ],
        )

        self.assertEqual(compiled.trace.candidate_count, 2)
        self.assertEqual(compiled.selected_items[0].content, "第一条")
        self.assertEqual(
            compiled.dropped_items,
            [
                {
                    "item_id": "same-id",
                    "source_ref": "messages/2",
                    "reason": "duplicate_item_id",
                }
            ],
        )

    def test_request_rejects_conflicting_scope(self) -> None:
        with self.assertRaisesRegex(ValueError, "user_id 与 scope 中的值冲突"):
            ContextRequest(
                current_input="继续",
                user_id="user-1",
                scope={"user_id": "user-2"},
            )


if __name__ == "__main__":
    unittest.main()
