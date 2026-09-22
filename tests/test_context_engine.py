"""ContextEngine 独立构建、摘要、引用和预算边界测试。"""

import json
import unittest
from dataclasses import asdict, replace
from datetime import UTC, datetime

from app.agent.context_engine.compiler import ContextCompiler
from app.agent.context_engine.compressor import TokenBoundaryCompressor
from app.agent.context_engine.contracts import (
    ContextConversationSummary,
    ContextItem,
    ContextPolicy,
    ContextRequest,
    ContextSourceKind,
    ContextSourceRef,
)
from app.agent.context_engine.deduplicator import ContextDeduplicator
from app.agent.context_engine.engine import ContextEngine
from app.agent.context_engine.history import ConversationHistoryManager
from app.agent.context_engine.planner import DeterministicContextPlanner
from app.agent.context_engine.resolver import ContextReferenceResolver
from app.agent.context_engine.selector import ContextSelector
from app.agent.context_engine.summarizer import (
    DeterministicConversationSummarizer,
)
from app.agent.memory.enums import MemoryScope, MemoryStatus, MemoryType
from app.agent.memory.interfaces import (
    MemoryAsset,
    MemoryRecord,
    MemorySearchResult,
    MemorySource,
)


class SimpleTokenCounter:
    """测试用确定性计数器；生产工厂仍使用真实 TiktokenCounter。"""

    def count_text(self, text: str) -> int:
        return len(text or "")

    def count_messages(self, messages) -> int:
        return 2 + sum(
            4
            + self.count_text(str(message.get("role") or ""))
            + self.count_text(str(message.get("content") or ""))
            for message in messages
        )

    def truncate(self, text: str, max_tokens: int, *, marker: str = "") -> str:
        if max_tokens <= 0:
            return ""
        if len(text) <= max_tokens:
            return text
        if len(marker) >= max_tokens:
            return marker[:max_tokens]
        return text[: max_tokens - len(marker)] + marker

    def split_text(self, text: str, max_tokens: int) -> list[str]:
        if max_tokens <= 0:
            raise ValueError("max_tokens 必须大于 0")
        return [
            text[start : start + max_tokens]
            for start in range(0, len(text), max_tokens)
        ]


def _record(
    memory_id: str,
    memory_type: MemoryType,
    content: str,
    *,
    index: int = 0,
    role: str = "user",
    version: int = 1,
    structured_data: dict | None = None,
    updated_at: datetime | None = None,
) -> MemoryRecord:
    data = {"message_index": index, "role": role}
    data.update(structured_data or {})
    now = updated_at or datetime.now(UTC)
    return MemoryRecord(
        memory_id=memory_id,
        user_id="user-1",
        memory_type=memory_type,
        scope=(
            MemoryScope.CONVERSATION
            if memory_type is MemoryType.WORKING
            else MemoryScope.USER
        ),
        conversation_id="conversation-1",
        project_id=None,
        content=content,
        structured_data=data,
        status=MemoryStatus.ACTIVE,
        version=version,
        importance=0.8,
        confidence=0.9,
        supersedes_memory_id=None,
        expires_at=None,
        created_at=now,
        updated_at=now,
    )


def _asset(asset_id: str, content: str = "附件中的销售数据") -> MemoryAsset:
    return MemoryAsset(
        asset_id=asset_id,
        user_id="user-1",
        conversation_id="conversation-1",
        modality="text",
        file_name=f"{asset_id}.csv",
        mime_type="text/csv",
        storage_uri=f"memory://{asset_id}",
        extracted_text=content,
        extraction_status="completed",
        index_status="completed",
    )


class FakeMemoryReader:
    """提供 ContextEngine 只读端口并记录实际召回类型。"""

    def __init__(
        self,
        *,
        working: list[MemoryRecord] | None = None,
        memories: dict[MemoryType, list[MemorySearchResult]] | None = None,
        assets: dict[str, MemoryAsset] | None = None,
    ) -> None:
        self.working = working or []
        self.memories = memories or {}
        self.assets = assets or {}
        self.search_calls: list[MemoryType] = []

    async def load_working(self, **_) -> list[MemoryRecord]:
        return list(self.working)

    async def search(self, *, memory_type: MemoryType, **_) -> list[MemorySearchResult]:
        self.search_calls.append(memory_type)
        return list(self.memories.get(memory_type, []))

    async def search_many(self, *, memory_types: list[MemoryType], **kwargs):
        values = []
        for memory_type in memory_types:
            values.extend(await self.search(memory_type=memory_type, **kwargs))
        return values

    async def get_asset(self, asset_id: str, user_id: str):
        asset = self.assets.get(asset_id)
        return asset if asset is not None and asset.user_id == user_id else None

    async def get_sources(self, memory_id: str, user_id: str):
        if user_id != "user-1":
            return []
        return [MemorySource("turn", f"source-{memory_id}")]


class InMemoryContextRepository:
    """测试用摘要和 trace 仓储。"""

    def __init__(self) -> None:
        self.summary: ContextConversationSummary | None = None
        self.started: dict[str, dict] = {}
        self.finished = []
        self.failed = []

    async def get_summary(self, user_id: str, conversation_id: str):
        summary = self.summary
        if (
            summary is not None
            and summary.user_id == user_id
            and summary.conversation_id == conversation_id
        ):
            return summary
        return None

    async def save_summary(self, summary: ContextConversationSummary):
        version = self.summary.version + 1 if self.summary else 1
        self.summary = replace(summary, version=version)
        return self.summary

    async def start_build(self, *, build_id: str, **values) -> None:
        self.started[build_id] = values

    async def finish_build(self, trace) -> None:
        self.finished.append(trace)

    async def fail_build(self, build_id: str, *, error_message: str) -> None:
        self.failed.append((build_id, error_message))


def _engine(
    reader: FakeMemoryReader,
    *,
    context_repository: InMemoryContextRepository | None = None,
    policy: ContextPolicy | None = None,
) -> tuple[ContextEngine, InMemoryContextRepository, SimpleTokenCounter]:
    resolved_repository = context_repository or InMemoryContextRepository()
    resolved_policy = policy or ContextPolicy(
        max_context_tokens=800,
        format_reserve_tokens=80,
        summary_max_tokens=120,
        max_item_tokens=200,
        min_compression_tokens=16,
    )
    counter = SimpleTokenCounter()
    summarizer = DeterministicConversationSummarizer(counter)
    resolver = ContextReferenceResolver(reader)
    return (
        ContextEngine(
            memory_reader=reader,
            context_repository=resolved_repository,
            planner=DeterministicContextPlanner(),
            resolver=resolver,
            history=ConversationHistoryManager(
                context_repository=resolved_repository,
                summarizer=summarizer,
                token_counter=counter,
                policy=resolved_policy,
            ),
            deduplicator=ContextDeduplicator(),
            selector=ContextSelector(
                policy=resolved_policy,
                token_counter=counter,
                compressor=TokenBoundaryCompressor(counter),
            ),
            compiler=ContextCompiler(counter),
            token_counter=counter,
            policy=resolved_policy,
        ),
        resolved_repository,
        counter,
    )


class ContextPlannerAndResolverTest(unittest.IsolatedAsyncioTestCase):
    async def test_plain_question_does_not_force_all_long_term_types(self) -> None:
        plan = await DeterministicContextPlanner().plan(
            ContextRequest(
                user_id="user-1",
                conversation_id="conversation-1",
                query="Python 中怎么排序列表？",
                system_instructions="回答问题",
            ),
            [],
            await ContextReferenceResolver(FakeMemoryReader()).resolve(
                ContextRequest(
                    user_id="user-1",
                    conversation_id="conversation-1",
                    query="Python 中怎么排序列表？",
                    system_instructions="回答问题",
                ),
                [],
            ),
        )

        self.assertEqual(plan.memory_types, ())
        self.assertFalse(plan.use_rag)

    async def test_resolves_previous_attachment_from_working_metadata(self) -> None:
        reader = FakeMemoryReader(assets={"asset-1": _asset("asset-1")})
        working = [
            _record(
                "message-1",
                MemoryType.WORKING,
                "请分析这个文件",
                structured_data={"asset_ids": ["asset-1"]},
            )
        ]
        result = await ContextReferenceResolver(reader).resolve(
            ContextRequest(
                user_id="user-1",
                conversation_id="conversation-1",
                query="继续分析上一个文件",
                system_instructions="回答问题",
            ),
            working,
        )

        self.assertEqual(result.asset_ids, ("asset-1",))
        self.assertEqual(result.historical_asset_ids, ("asset-1",))
        self.assertEqual(result.unresolved_references, ())

    async def test_explicit_inaccessible_attachment_fails_closed(self) -> None:
        with self.assertRaises(PermissionError):
            await ContextReferenceResolver(FakeMemoryReader()).resolve(
                ContextRequest(
                    user_id="user-1",
                    conversation_id="conversation-1",
                    query="分析附件",
                    system_instructions="回答问题",
                    asset_ids=("asset-not-owned",),
                ),
                [],
            )

    async def test_ambiguous_attachment_reference_is_not_guessed(self) -> None:
        reader = FakeMemoryReader(
            assets={
                "asset-1": _asset("asset-1"),
                "asset-2": _asset("asset-2"),
            }
        )
        working = [
            _record(
                "message-1",
                MemoryType.WORKING,
                "第一个文件",
                index=1,
                structured_data={"asset_ids": ["asset-1"]},
            ),
            _record(
                "message-2",
                MemoryType.WORKING,
                "第二个文件",
                index=2,
                structured_data={"asset_ids": ["asset-2"]},
            ),
        ]
        result = await ContextReferenceResolver(reader).resolve(
            ContextRequest(
                user_id="user-1",
                conversation_id="conversation-1",
                query="分析文件里的销售额",
                system_instructions="回答问题",
            ),
            working,
        )

        self.assertEqual(result.asset_ids, ())
        self.assertIn("无法唯一确定", result.unresolved_references[0])

    async def test_resolves_ordinal_attachment_from_same_upload_group(self) -> None:
        reader = FakeMemoryReader(
            assets={
                "asset-1": _asset("asset-1"),
                "asset-2": _asset("asset-2"),
            }
        )
        working = [
            _record(
                "message-1",
                MemoryType.WORKING,
                "请比较这两张截图",
                structured_data={"asset_ids": ["asset-1", "asset-2"]},
            )
        ]

        result = await ContextReferenceResolver(reader).resolve(
            ContextRequest(
                user_id="user-1",
                conversation_id="conversation-1",
                query="第二张截图中销售额是多少？",
                system_instructions="回答问题",
            ),
            working,
        )

        self.assertEqual(result.asset_ids, ("asset-2",))
        self.assertEqual(result.historical_asset_ids, ("asset-2",))


class ContextHistoryAndSelectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_summary_only_processes_newly_evicted_messages(self) -> None:
        counter = SimpleTokenCounter()
        context_repository = InMemoryContextRepository()
        policy = ContextPolicy(
            max_context_tokens=300,
            format_reserve_tokens=20,
            recent_history_ratio=0.5,
            summary_max_tokens=80,
            max_item_tokens=100,
            min_compression_tokens=8,
        )
        manager = ConversationHistoryManager(
            context_repository=context_repository,
            summarizer=DeterministicConversationSummarizer(counter),
            token_counter=counter,
            policy=policy,
        )
        working = [
            _record(
                f"message-{index}",
                MemoryType.WORKING,
                f"第 {index} 条消息 " + "销售数据 " * 10,
                index=index,
                role="user" if index % 2 == 0 else "assistant",
            )
            for index in range(8)
        ]

        first = await manager.prepare(
            user_id="user-1",
            conversation_id="conversation-1",
            query="继续",
            working=working,
            token_budget=100,
        )
        first_covered = first.summary.covered_through_index
        extended = [
            *working,
            _record(
                "message-8",
                MemoryType.WORKING,
                "第 8 条消息 " + "销售数据 " * 10,
                index=8,
            ),
            _record(
                "message-9",
                MemoryType.WORKING,
                "第 9 条消息 " + "销售数据 " * 10,
                index=9,
            ),
        ]
        second = await manager.prepare(
            user_id="user-1",
            conversation_id="conversation-1",
            query="继续",
            working=extended,
            token_budget=100,
        )

        self.assertTrue(first.summary_updated)
        self.assertTrue(second.summary_updated)
        self.assertGreater(second.summary.covered_through_index, first_covered)
        self.assertEqual(second.summary.version, 2)
        recent_indexes = [
            item.structured_data["message_index"]
            for item in second.items
            if item.source_kind is ContextSourceKind.WORKING
        ]
        self.assertTrue(
            all(
                index > second.summary.covered_through_index for index in recent_indexes
            )
        )

    def test_deduplicator_keeps_latest_typed_version(self) -> None:
        old = ContextItem(
            item_id="old",
            source_kind=ContextSourceKind.SEMANTIC,
            content="用户偏好英文",
            token_count=5,
            structured_data={"fact_key": "user.language", "version": 1},
        )
        new = replace(
            old,
            item_id="new",
            content="用户偏好中文",
            structured_data={"fact_key": "user.language", "version": 2},
        )

        result = ContextDeduplicator().deduplicate([old, new])

        self.assertEqual([item.item_id for item in result.items], ["new"])
        self.assertEqual(result.decisions[0].item_id, "old")

    async def test_required_item_survives_higher_scoring_optional_item(self) -> None:
        counter = SimpleTokenCounter()
        policy = ContextPolicy(
            max_context_tokens=120,
            format_reserve_tokens=10,
            max_item_tokens=50,
            min_compression_tokens=5,
        )
        selector = ContextSelector(
            policy=policy,
            token_counter=counter,
            compressor=TokenBoundaryCompressor(counter),
        )
        required = ContextItem(
            item_id="asset",
            source_kind=ContextSourceKind.PERCEPTUAL,
            content="必须保留的附件内容 " * 20,
            token_count=counter.count_text("必须保留的附件内容 " * 20),
            relevance=0.3,
            required=True,
            source_refs=(ContextSourceRef("asset", "asset-1"),),
        )
        optional = ContextItem(
            item_id="semantic",
            source_kind=ContextSourceKind.SEMANTIC,
            content="高分语义事实 " * 20,
            token_count=counter.count_text("高分语义事实 " * 20),
            relevance=1.0,
            importance=1.0,
            confidence=1.0,
            priority=100,
        )

        result = await selector.select([optional, required], token_budget=55)

        self.assertIn("asset", [item.item_id for item in result.items])
        asset_decision = next(
            item for item in result.decisions if item.item_id == "asset"
        )
        self.assertTrue(asset_decision.selected)
        self.assertTrue(asset_decision.compressed)

    async def test_required_items_share_budget_instead_of_dropping_later_items(
        self,
    ) -> None:
        counter = SimpleTokenCounter()
        policy = ContextPolicy(
            max_context_tokens=160,
            format_reserve_tokens=10,
            max_item_tokens=100,
            min_compression_tokens=10,
        )
        selector = ContextSelector(
            policy=policy,
            token_counter=counter,
            compressor=TokenBoundaryCompressor(counter),
        )
        required = [
            ContextItem(
                item_id=f"asset-{index}",
                source_kind=ContextSourceKind.PERCEPTUAL,
                content=f"附件 {index} " * 40,
                token_count=counter.count_text(f"附件 {index} " * 40),
                required=True,
            )
            for index in (1, 2)
        ]

        result = await selector.select(required, token_budget=100)

        self.assertEqual(
            {item.item_id for item in result.items}, {"asset-1", "asset-2"}
        )
        self.assertLessEqual(result.used_tokens, 100)


class ContextEngineBuildTest(unittest.IsolatedAsyncioTestCase):
    async def test_build_compiles_stable_messages_and_trace_without_content(
        self,
    ) -> None:
        secret_query = "请按我的口径继续分析绝密销售数据"
        semantic = _record(
            "semantic-1",
            MemoryType.SEMANTIC,
            "绝密口径内容不能进入 trace",
            structured_data={"fact_key": "metric.gmv.definition"},
        )
        reader = FakeMemoryReader(
            working=[
                _record(
                    "message-1",
                    MemoryType.WORKING,
                    "上一轮问题",
                    index=0,
                ),
                _record(
                    "message-2",
                    MemoryType.WORKING,
                    "上一轮回答",
                    index=1,
                    role="assistant",
                ),
            ],
            memories={
                MemoryType.SEMANTIC: [MemorySearchResult(semantic, 0.9, 0.95, "vector")]
            },
        )
        engine, context_repository, _ = _engine(reader)

        result = await engine.build(
            ContextRequest(
                user_id="user-1",
                conversation_id="conversation-1",
                query=secret_query,
                system_instructions="你是数据助手",
                memory_types=(MemoryType.SEMANTIC,),
            )
        )

        self.assertEqual(result.messages[0]["role"], "system")
        self.assertEqual(result.messages[-1], {"role": "user", "content": secret_query})
        self.assertIn("绝密口径", result.sections.known_facts[0])
        self.assertEqual(reader.search_calls, [MemoryType.SEMANTIC])
        trace_json = json.dumps(asdict(result.trace), ensure_ascii=False, default=str)
        self.assertNotIn(secret_query, trace_json)
        self.assertNotIn(semantic.content, trace_json)
        self.assertIn("source-semantic-1", trace_json)
        self.assertEqual(context_repository.finished, [result.trace])

    async def test_final_compiled_messages_never_exceed_token_budget(self) -> None:
        working = [
            _record(
                f"message-{index}",
                MemoryType.WORKING,
                "很长的历史消息 " * 40,
                index=index,
                role="user" if index % 2 == 0 else "assistant",
            )
            for index in range(10)
        ]
        policy = ContextPolicy(
            max_context_tokens=180,
            format_reserve_tokens=20,
            working_history_ratio=0.7,
            recent_history_ratio=0.6,
            summary_max_tokens=50,
            max_item_tokens=60,
            min_compression_tokens=8,
        )
        engine, _, counter = _engine(FakeMemoryReader(working=working), policy=policy)

        result = await engine.build(
            ContextRequest(
                user_id="user-1",
                conversation_id="conversation-1",
                query="继续",
                system_instructions="简洁回答",
                token_budget=180,
            )
        )

        self.assertLessEqual(result.token_count, 180)
        self.assertEqual(result.token_count, counter.count_messages(result.messages))
        self.assertLessEqual(result.trace.final_token_count, result.trace.token_budget)

    async def test_explicit_attachment_is_injected_without_perceptual_memory(
        self,
    ) -> None:
        reader = FakeMemoryReader(assets={"asset-1": _asset("asset-1")})
        engine, _, _ = _engine(reader)

        result = await engine.build(
            ContextRequest(
                user_id="user-1",
                conversation_id="conversation-1",
                query="分析这个文件",
                system_instructions="你是数据助手",
                asset_ids=("asset-1",),
            )
        )

        self.assertEqual(result.resolved_asset_ids, ("asset-1",))
        self.assertTrue(result.sections.attachments)
        self.assertIn("附件中的销售数据", result.sections.attachments[0])


if __name__ == "__main__":
    unittest.main()
