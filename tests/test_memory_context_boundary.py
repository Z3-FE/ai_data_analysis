"""验证 Memory 面向 ContextEngine 的稳定只读边界。"""

import unittest
from datetime import UTC, datetime, timedelta

from app.agent.memory.enums import MemoryScope, MemoryStatus, MemoryType
from app.agent.memory.interfaces import (
    MemoryAsset,
    MemoryRecord,
    MemorySearchResult,
    MemorySource,
)
from app.agent.memory.manager import MemoryManager
from app.agent.memory.types.working import WorkingMemory


def _record(
    memory_id: str,
    memory_type: MemoryType,
    *,
    content: str = "记忆内容",
) -> MemoryRecord:
    """构造 ContextEngine 读路径使用的最小领域记录。"""
    now = datetime.now(UTC)
    return MemoryRecord(
        memory_id=memory_id,
        user_id="user-1",
        memory_type=memory_type,
        scope=MemoryScope.USER,
        conversation_id=None,
        project_id=None,
        content=content,
        structured_data={},
        status=MemoryStatus.ACTIVE,
        version=1,
        importance=0.5,
        confidence=1.0,
        supersedes_memory_id=None,
        expires_at=None,
        created_at=now,
        updated_at=now,
    )


class FakeMemory:
    """记录 Manager 将检索路由到哪些记忆类型。"""

    def __init__(self, memory_type: MemoryType) -> None:
        self.memory_type = memory_type
        self.search_calls: list[dict] = []

    async def search(self, **kwargs):
        self.search_calls.append(kwargs)
        record = _record(f"{self.memory_type.value}-1", self.memory_type)
        return [
            MemorySearchResult(
                memory=record,
                similarity=0.8,
                score=0.8,
                source=self.memory_type.value,
            )
        ]


class FakeWorkingMemory:
    """只实现 Manager 读取 Working Memory 所需的 load。"""

    async def load(self, **kwargs):
        self.load_kwargs = kwargs
        return [_record("working-1", MemoryType.WORKING)]


class FakeRepository:
    """只实现附件、来源和维护边界测试所需的仓储端口。"""

    def __init__(self) -> None:
        self.asset = MemoryAsset(
            asset_id="asset-1",
            user_id="user-1",
            conversation_id="conversation-1",
            modality="text",
            file_name="note.txt",
            mime_type="text/plain",
            storage_uri="memory://asset-1",
            extracted_text="附件文本",
            extraction_status="completed",
            index_status="completed",
        )

    async def get_asset(self, asset_id: str, user_id: str):
        if asset_id == self.asset.asset_id and user_id == self.asset.user_id:
            return self.asset
        return None

    async def list_sources(self, memory_id: str, user_id: str):
        if memory_id == "semantic-1" and user_id == "user-1":
            return [MemorySource("turn", "turn-1")]
        return []

    async def get(self, memory_id: str, user_id: str):
        if memory_id == "working-1" and user_id == "user-1":
            return _record(memory_id, MemoryType.WORKING)
        return None


class FakeScalarResult:
    """模拟 SQLAlchemy scalars(...).all() 的最小结果对象。"""

    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeSession:
    """按查询参数执行 conversation_messages 的最小异步数据库替身。"""

    def __init__(self, rows):
        self.rows = rows

    async def scalars(self, statement):
        params = statement.compile().params
        conversation_id = next(
            value for key, value in params.items() if key.startswith("conversation_id")
        )
        user_id = next(
            value for key, value in params.items() if key.startswith("user_id")
        )
        if user_id != "user-1":
            return FakeScalarResult([])
        rows = [
            row
            for row in self.rows
            if row.conversation_id == conversation_id and row.user_id == user_id
        ]
        rows.sort(key=lambda row: (row.created_at, row.sequence_no))
        return FakeScalarResult(rows)


class FakeSessionFactory:
    """提供 async with session_factory() 语义的数据库替身。"""

    def __init__(self, rows):
        self.rows = rows

    def __call__(self):
        factory = self

        class SessionContext:
            async def __aenter__(self):
                return FakeSession(factory.rows)

            async def __aexit__(self, exc_type, exc, traceback):
                return False

        return SessionContext()


def _message_rows():
    """构造 conversation_messages 的数据库行。"""
    from app.models.agent_history import ConversationMessageModel

    base_time = datetime.now(UTC).replace(tzinfo=None)
    return [
        ConversationMessageModel(
            message_id="message-1",
            conversation_id="conversation-1",
            turn_id="turn-1",
            user_id="user-1",
            role="user",
            message_type="text",
            sequence_no=1,
            content="第一条",
            message_metadata={},
            created_at=base_time,
        ),
        ConversationMessageModel(
            message_id="message-2",
            conversation_id="conversation-1",
            turn_id="turn-1",
            user_id="user-1",
            role="assistant",
            message_type="text",
            sequence_no=2,
            content="第二条",
            message_metadata={},
            created_at=base_time + timedelta(seconds=1),
        ),
        ConversationMessageModel(
            message_id="message-3",
            conversation_id="conversation-1",
            turn_id="turn-2",
            user_id="user-1",
            role="user",
            message_type="text",
            sequence_no=1,
            content="第三条",
            message_metadata={
                "turn_id": "turn-2",
                "asset_ids": ["asset-1", "asset-2"],
            },
            created_at=base_time + timedelta(seconds=2),
        ),
        ConversationMessageModel(
            message_id="message-other",
            conversation_id="conversation-1",
            turn_id="turn-other",
            user_id="other-user",
            role="user",
            message_type="text",
            sequence_no=1,
            content="其他用户的内容",
            message_metadata={},
            created_at=base_time + timedelta(seconds=3),
        ),
    ]

class MemoryManagerContextBoundaryTest(unittest.IsolatedAsyncioTestCase):
    """ContextEngine 只通过 Manager 读接口选择和读取记忆。"""

    def _manager(self):
        self.semantic = FakeMemory(MemoryType.SEMANTIC)
        self.episodic = FakeMemory(MemoryType.EPISODIC)
        self.perceptual = FakeMemory(MemoryType.PERCEPTUAL)
        self.working = FakeWorkingMemory()
        self.repository = FakeRepository()
        return MemoryManager(
            semantic=self.semantic,
            episodic=self.episodic,
            perceptual=self.perceptual,
            working=self.working,
            repository=self.repository,
        )

    async def test_search_many_only_calls_requested_memory_types(self):
        manager = self._manager()

        results = await manager.search_many(
            memory_types=[MemoryType.SEMANTIC, MemoryType.EPISODIC],
            user_id="user-1",
            query="项目规则",
        )

        self.assertEqual(len(results), 2)
        self.assertEqual(len(self.semantic.search_calls), 1)
        self.assertEqual(len(self.episodic.search_calls), 1)
        self.assertEqual(self.perceptual.search_calls, [])

    async def test_working_asset_and_source_reads_delegate(self):
        manager = self._manager()

        working = await manager.load_working(
            user_id="user-1",
            conversation_id="conversation-1",
            limit=12,
        )
        asset = await manager.get_asset("asset-1", "user-1")
        sources = await manager.get_sources("semantic-1", "user-1")

        self.assertEqual(working[0].memory_id, "working-1")
        self.assertEqual(self.working.load_kwargs["limit"], 12)
        self.assertEqual(asset.asset_id, "asset-1")
        self.assertEqual(
            [(item.source_type, item.source_id) for item in sources],
            [("turn", "turn-1")],
        )

    async def test_working_memory_rejects_mismatched_user(self):
        records = await WorkingMemory(FakeSessionFactory(_message_rows())).load(
            user_id="other-user", conversation_id="conversation-1"
        )

        self.assertEqual(records, [])

    async def test_working_memory_records_are_chronological(self):
        records = await WorkingMemory(FakeSessionFactory(_message_rows())).load(
            user_id="user-1", conversation_id="conversation-1"
        )

        self.assertEqual(
            [record.content for record in records], ["第一条", "第二条", "第三条"]
        )
        self.assertEqual(
            [record.structured_data["message_index"] for record in records], [0, 1, 2]
        )

    async def test_working_memory_preserves_context_reference_metadata(self):
        records = await WorkingMemory(FakeSessionFactory(_message_rows())).load(
            user_id="user-1", conversation_id="conversation-1"
        )

        self.assertEqual(records[-1].structured_data["message_index"], 2)
        self.assertEqual(
            records[-1].structured_data["asset_ids"], ["asset-1", "asset-2"]
        )


if __name__ == "__main__":
    unittest.main()
