"""Data Agent 记忆 Provider 和 Manager 的后端单元测试。"""

import unittest
from collections.abc import Iterable

from app.agent.memory import (
    MemoryItem,
    MemoryManager,
    MemoryMatch,
    MemoryReadRequest,
    MemoryScope,
    MemorySource,
    MemoryStatus,
    MemoryType,
    PostgreSQLMemoryProvider,
)
from app.agent.memory.postgres import _model_from_item


class _ScalarResult:
    """模拟 SQLAlchemy scalars() 返回的结果对象。"""

    def __init__(self, models: Iterable[object]) -> None:
        # 保存查询结果，测试只关心 Provider 对结果的映射。
        self.models = list(models)

    def all(self) -> list[object]:
        # 模拟 ScalarResult.all()。
        return self.models


class _FakeSession:
    """不连接真实 PostgreSQL 的异步 Session 测试替身。"""

    def __init__(self, scalar_model=None, search_models=()) -> None:
        # scalar_model 用于 update/archive，search_models 用于 search。
        self.scalar_model = scalar_model
        self.search_models = list(search_models)
        self.added = []
        self.statements = []
        self.commit_count = 0

    async def __aenter__(self):
        # 让替身支持 async with session_factory()。
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        # 测试替身没有需要释放的连接。
        return None

    def add(self, model) -> None:
        # 记录 Provider 交给 Session 的 ORM 实例。
        self.added.append(model)

    async def commit(self) -> None:
        # 记录写入操作确实提交。
        self.commit_count += 1

    async def refresh(self, model) -> None:
        # 测试替身不需要从数据库刷新默认字段。
        return None

    async def scalar(self, statement):
        # 记录语句，具体边界由测试中的模型和编译结果验证。
        self.statements.append(statement)
        return self.scalar_model

    async def scalars(self, statement):
        # 记录语句并返回可迭代查询结果。
        self.statements.append(statement)
        return _ScalarResult(self.search_models)


class _FakeSessionFactory:
    """每次调用创建一个 Session，并保留实例供断言使用。"""

    def __init__(self, scalar_model=None, search_models=()) -> None:
        # Provider 每次操作都通过工厂获得独立 Session。
        self.scalar_model = scalar_model
        self.search_models = list(search_models)
        self.sessions = []

    def __call__(self):
        # 记录本次数据库操作使用的 Session。
        session = _FakeSession(self.scalar_model, self.search_models)
        self.sessions.append(session)
        return session


class _RecordingProvider:
    """用于验证 MemoryManager 类型化入口的最小 Provider。"""

    def __init__(self) -> None:
        # 记录 Manager 传入的请求和记忆对象。
        self.added: list[MemoryItem] = []
        self.requests: list[MemoryReadRequest] = []

    async def add(self, item: MemoryItem) -> MemoryItem:
        # 返回原对象，模拟存储成功。
        self.added.append(item)
        return item

    async def search(self, request: MemoryReadRequest) -> list[MemoryMatch]:
        # 返回空结果，测试只校验请求契约。
        self.requests.append(request)
        return []

    async def update(self, item: MemoryItem) -> MemoryItem:
        # Manager 委托更新时返回原对象。
        return item

    async def archive(self, memory_id, *, scope, reason=None):
        # Manager 委托归档时没有额外行为。
        return None


class AgentMemoryProviderTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.scope = MemoryScope(
            user_id="user-1",
            tenant_id="tenant-1",
            agent_id="data-agent",
            project_id="project-1",
            conversation_id="conversation-1",
        )
        self.source = MemorySource(
            source_type="conversation_message",
            source_id="message-1",
            turn_id="turn-1",
            message_id="message-1",
            metadata={"extractor": "test"},
        )

    def _item(
        self,
        memory_type: MemoryType,
        content: str = "一条记忆事实。",
    ) -> MemoryItem:
        # 统一创建包含完整作用域和来源的测试数据。
        return MemoryItem(
            memory_type=memory_type,
            content=content,
            scope=self.scope,
            source=self.source,
            structured_data={"asset_id": "asset-1"},
            metadata={"test_case": "provider"},
            importance=0.8,
            confidence=0.9,
        )

    async def test_manager_saves_each_memory_type(self) -> None:
        provider = _RecordingProvider()
        manager = MemoryManager(provider)

        await manager.save_working(
            "当前活动任务。", scope=self.scope, source=self.source
        )
        await manager.save_episodic(
            "历史分析经历。", scope=self.scope, source=self.source
        )
        await manager.save_semantic(
            "已确认的指标定义。", scope=self.scope, source=self.source
        )
        await manager.save_perceptual(
            "图片中的图表观察。",
            scope=self.scope,
            source=self.source,
            structured_data={"asset_id": "asset-1"},
        )

        self.assertEqual(
            [item.memory_type for item in provider.added],
            [
                MemoryType.WORKING,
                MemoryType.EPISODIC,
                MemoryType.SEMANTIC,
                MemoryType.PERCEPTUAL,
            ],
        )
        self.assertEqual(provider.added[-1].structured_data["asset_id"], "asset-1")

    async def test_manager_working_read_requires_exact_conversation_scope(self) -> None:
        provider = _RecordingProvider()
        manager = MemoryManager(provider)

        await manager.read_working(scope=self.scope)

        request = provider.requests[-1]
        self.assertTrue(request.exact_conversation)
        self.assertEqual(request.scope.conversation_id, "conversation-1")
        self.assertEqual(request.memory_types, frozenset({MemoryType.WORKING}))

    async def test_manager_rejects_empty_memory_type_set(self) -> None:
        manager = MemoryManager(_RecordingProvider())

        with self.assertRaisesRegex(ValueError, "memory_types 不能为空"):
            await manager.read(scope=self.scope, memory_types=set())

    async def test_provider_add_maps_domain_fields_to_orm_and_back(self) -> None:
        factory = _FakeSessionFactory()
        provider = PostgreSQLMemoryProvider(factory)
        item = self._item(MemoryType.PERCEPTUAL, "图片观察内容。")

        saved = await provider.add(item)

        session = factory.sessions[0]
        model = session.added[0]
        self.assertEqual(session.commit_count, 1)
        self.assertEqual(model.memory_id, item.memory_id)
        self.assertEqual(model.memory_type, "perceptual")
        self.assertEqual(model.user_id, "user-1")
        self.assertEqual(model.source_turn_id, "turn-1")
        self.assertEqual(model.structured_data, {"asset_id": "asset-1"})
        self.assertEqual(saved.to_dict(), item.to_dict())

    async def test_provider_search_returns_keyword_matches_and_asset_filter(self) -> None:
        model = _model_from_item(self._item(MemoryType.PERCEPTUAL, "销售趋势图。"))
        factory = _FakeSessionFactory(search_models=[model])
        provider = PostgreSQLMemoryProvider(factory)
        request = MemoryReadRequest(
            scope=self.scope,
            query="销售%_\\趋势",
            memory_types={MemoryType.PERCEPTUAL},
            asset_ids=("asset-1",),
            limit=3,
        )

        matches = await provider.search(request)

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].retrieval_mode, "keyword")
        self.assertEqual(matches[0].item.memory_id, model.memory_id)
        compiled = str(
            factory.sessions[0].statements[0].compile(
                dialect=__import__("sqlalchemy.dialects.postgresql", fromlist=["dialect"]).dialect()
            )
        )
        self.assertIn("structured_data", compiled)
        self.assertIn("ILIKE", compiled)

    async def test_provider_working_search_uses_exact_conversation_filter(self) -> None:
        model = _model_from_item(self._item(MemoryType.WORKING))
        factory = _FakeSessionFactory(search_models=[model])
        provider = PostgreSQLMemoryProvider(factory)
        request = MemoryReadRequest(
            scope=self.scope,
            memory_types={MemoryType.WORKING},
            exact_conversation=True,
        )

        await provider.search(request)

        compiled = str(
            factory.sessions[0].statements[0].compile(
                dialect=__import__("sqlalchemy.dialects.postgresql", fromlist=["dialect"]).dialect()
            )
        )
        self.assertIn("agent_memories.conversation_id =", compiled)
        self.assertNotIn("agent_memories.conversation_id IS NULL", compiled)
        self.assertNotIn(" OR agent_memories.conversation_id =", compiled)

    async def test_provider_update_rejects_cross_scope_item(self) -> None:
        stored_model = _model_from_item(self._item(MemoryType.SEMANTIC))
        factory = _FakeSessionFactory(scalar_model=stored_model)
        provider = PostgreSQLMemoryProvider(factory)
        foreign_item = self._item(MemoryType.SEMANTIC, "不应被更新。")
        foreign_item.scope = MemoryScope(user_id="another-user")

        with self.assertRaisesRegex(PermissionError, "不能更新其他作用域"):
            await provider.update(foreign_item)

        self.assertEqual(factory.sessions[0].commit_count, 0)

    async def test_provider_archive_keeps_record_and_reason(self) -> None:
        stored_model = _model_from_item(self._item(MemoryType.EPISODIC))
        factory = _FakeSessionFactory(scalar_model=stored_model)
        provider = PostgreSQLMemoryProvider(factory)

        archived = await provider.archive(
            stored_model.memory_id,
            scope=self.scope,
            reason="用户要求移除",
        )

        self.assertIsNotNone(archived)
        self.assertEqual(archived.status, MemoryStatus.ARCHIVED)
        self.assertEqual(archived.metadata["archive_reason"], "用户要求移除")
        self.assertEqual(factory.sessions[0].commit_count, 1)


if __name__ == "__main__":
    unittest.main()
