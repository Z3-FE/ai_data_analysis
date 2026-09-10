"""M3 记忆形成与治理的行为测试。"""

import asyncio
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace

from app.agent.memory.contracts import (
    GovernedCandidate,
    MemoryCandidate,
    MemoryDecision,
    TurnMemoryInput,
)
from app.agent.memory.eligibility import MemoryEligibilityEvaluator
from app.agent.memory.enums import (
    MemoryDecisionAction,
    MemoryFormationStatus,
    MemoryFormationTrigger,
    MemoryScope,
    MemoryStatus,
    MemoryType,
)
from app.agent.memory.explicit_extractor import ExplicitMemoryExtractor
from app.agent.memory.factory import build_memory_runtime
from app.agent.memory.formation_service import MemoryFormationService
from app.agent.memory.governance import MemoryGovernance, MemoryGovernanceError
from app.agent.memory.interfaces import (
    MemoryAsset,
    MemoryCreate,
    MemoryRecord,
    MemoryWriteResult,
)
from app.agent.memory.llm_extractor import LlmMemoryExtractor
from app.agent.memory.writer import MemoryWriter
from app.agent.memory.write_policy import resolve_memory_write_identity
from app.repositories.memory.postgres_memory_repository import _same_payload
from app.services.agent_service import AgentService


def _turn(**changes) -> TurnMemoryInput:
    """构造一轮最小的已完成 Agent 输入。"""
    values = {
        "user_id": "user-1",
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "run_id": "run-1",
        "input_text": "普通问题",
        "assistant_content": "普通回答",
        "execution_mode": "daily_chat",
        "status": "completed",
        "output_type": "text",
        "output_payload": {"message": "普通回答"},
    }
    values.update(changes)
    return TurnMemoryInput(**values)


class FakeFormationRepository:
    """提供形成服务需要的最小仓储端口。"""

    def __init__(
        self,
        *,
        sources_exist: bool = True,
        assets: dict[str, MemoryAsset] | None = None,
    ) -> None:
        self.sources_exist = sources_exist
        self.assets = assets or {}
        self.runs: dict[str, dict] = {}

    async def create_formation_run(self, payload: dict) -> None:
        self.runs[payload["formation_run_id"]] = dict(payload)

    async def update_formation_run(self, formation_run_id: str, **changes) -> None:
        self.runs.setdefault(formation_run_id, {}).update(changes)

    async def source_exists(self, **_) -> bool:
        return self.sources_exist

    async def get_asset(self, asset_id: str, user_id: str):
        asset = self.assets.get(asset_id)
        return asset if asset is not None and asset.user_id == user_id else None


class FakeWriter:
    """记录候选写入并返回创建决定。"""

    def __init__(self) -> None:
        self.governed: list[GovernedCandidate] = []

    async def write(self, governed) -> MemoryDecision:
        self.governed.append(governed)
        return MemoryDecision(
            candidate_id=governed.candidate.candidate_id,
            memory_type=governed.candidate.memory_type,
            action=MemoryDecisionAction.CREATED,
            reason="测试写入",
            memory_id="memory-1",
            fact_key=governed.candidate.fact_key,
        )


class FakeLlmExtractor:
    """返回固定候选，验证自动形成确实异步执行。"""

    def __init__(self, candidate: MemoryCandidate) -> None:
        self.candidate = candidate
        self.started = asyncio.Event()

    async def extract(self, _turn, *, mode="automatic") -> list[MemoryCandidate]:
        self.mode = mode
        self.started.set()
        return [self.candidate]


class MemoryFormationTest(unittest.IsolatedAsyncioTestCase):
    """覆盖 M3 的触发、治理和后台形成边界。"""

    def test_eligibility_skips_plain_chat_and_accepts_explicit_request(self) -> None:
        evaluator = MemoryEligibilityEvaluator(automatic_formation_enabled=False)
        skipped = evaluator.evaluate(_turn())
        explicit = evaluator.evaluate(
            _turn(input_text="请记住我叫张三")
        )

        self.assertFalse(skipped.eligible)
        self.assertEqual(skipped.trigger, MemoryFormationTrigger.SKIPPED)
        self.assertTrue(explicit.eligible)
        self.assertEqual(explicit.trigger, MemoryFormationTrigger.EXPLICIT)

    def test_negative_memory_request_is_skipped(self) -> None:
        result = MemoryEligibilityEvaluator().evaluate(
            _turn(input_text="不要记住我的密码")
        )
        self.assertFalse(result.eligible)
        self.assertEqual(result.trigger, MemoryFormationTrigger.SKIPPED)

    async def test_runtime_without_llm_keeps_explicit_memory_only(self) -> None:
        runtime = await build_memory_runtime(
            session_factory=object(),
            initialize_graph_schema=False,
        )

        explicit = runtime.formation_service.eligibility.evaluate(
            _turn(input_text="请记住我叫张三")
        )
        automatic = runtime.formation_service.eligibility.evaluate(
            _turn(
                input_text="分析2017年销售额",
                execution_mode="analysis",
                output_type="rendered_report",
            )
        )

        self.assertTrue(explicit.eligible)
        self.assertEqual(explicit.trigger, MemoryFormationTrigger.EXPLICIT)
        self.assertFalse(automatic.eligible)
        self.assertEqual(automatic.trigger, MemoryFormationTrigger.SKIPPED)

    def test_explicit_extractor_creates_semantic_candidate_without_llm(self) -> None:
        candidates = ExplicitMemoryExtractor().extract(
            _turn(input_text="请记住我叫张三")
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].memory_type, MemoryType.SEMANTIC)
        self.assertEqual(candidates[0].fact_key, "")
        self.assertEqual(candidates[0].value, "我叫张三")

    async def test_governance_rejects_working_and_sensitive_candidates(self) -> None:
        repository = FakeFormationRepository()
        governance = MemoryGovernance(repository)
        working = MemoryCandidate(
            memory_type=MemoryType.WORKING,
            content="临时上下文",
        )
        sensitive = MemoryCandidate(
            memory_type=MemoryType.SEMANTIC,
            content="用户的 api_key 是 secret-value",
        )

        with self.assertRaises(MemoryGovernanceError):
            await governance.govern(working, _turn())
        with self.assertRaises(MemoryGovernanceError):
            await governance.govern(sensitive, _turn())

    async def test_governance_rejects_sensitive_structured_data(self) -> None:
        repository = FakeFormationRepository()
        governance = MemoryGovernance(repository)
        candidate = MemoryCandidate(
            memory_type=MemoryType.SEMANTIC,
            content="用户配置了第三方服务",
            structured_data={"api_key": "secret-value"},
        )

        with self.assertRaises(MemoryGovernanceError):
            await governance.govern(candidate, _turn())

    async def test_governance_rejects_low_confidence_and_unbound_perceptual(
        self,
    ) -> None:
        repository = FakeFormationRepository()
        governance = MemoryGovernance(repository)
        low_confidence = MemoryCandidate(
            memory_type=MemoryType.SEMANTIC,
            content="可能偏好中文",
            confidence=0.5,
        )
        perceptual = MemoryCandidate(
            memory_type=MemoryType.PERCEPTUAL,
            content="附件中的文本",
            confidence=0.9,
        )

        with self.assertRaises(MemoryGovernanceError):
            await governance.govern(low_confidence, _turn())
        with self.assertRaises(MemoryGovernanceError):
            await governance.govern(perceptual, _turn())

    async def test_governance_rejects_untrusted_declared_source(self) -> None:
        repository = FakeFormationRepository(sources_exist=False)
        governance = MemoryGovernance(repository)
        candidate = MemoryCandidate(
            memory_type=MemoryType.SEMANTIC,
            content="用户偏好中文",
            source_refs=[{"source_type": "turn", "source_id": "other-turn"}],
        )

        with self.assertRaises(MemoryGovernanceError):
            await governance.govern(candidate, _turn())

    async def test_explicit_request_is_processed_synchronously_and_audited(
        self,
    ) -> None:
        repository = FakeFormationRepository()
        writer = FakeWriter()
        service = MemoryFormationService(
            repository=repository,
            governance=MemoryGovernance(repository),
            writer=writer,
        )

        result = await service.submit(_turn(input_text="请记住我叫张三"))

        self.assertEqual(result.status, MemoryFormationStatus.COMPLETED)
        self.assertEqual(result.trigger, MemoryFormationTrigger.EXPLICIT)
        self.assertEqual(result.accepted_count, 1)
        self.assertEqual(len(writer.governed), 1)
        audit = repository.runs[result.formation_run_id]
        self.assertEqual(audit["status"], MemoryFormationStatus.COMPLETED.value)
        self.assertEqual(audit["candidate_count"], 1)

    async def test_automatic_candidate_runs_in_background(self) -> None:
        repository = FakeFormationRepository()
        writer = FakeWriter()
        extractor = FakeLlmExtractor(
            MemoryCandidate(
                memory_type=MemoryType.EPISODIC,
                content="本次分析成功完成",
                fact_key="task.last_success",
            )
        )
        service = MemoryFormationService(
            repository=repository,
            governance=MemoryGovernance(repository),
            writer=writer,
            llm_extractor=extractor,
        )

        result = await service.submit(
            _turn(
                input_text="分析2017年销售额",
                execution_mode="analysis",
                output_type="rendered_report",
            )
        )

        self.assertEqual(result.status, MemoryFormationStatus.PENDING)
        await service.close()
        self.assertTrue(extractor.started.is_set())
        audit = repository.runs[result.formation_run_id]
        self.assertEqual(audit["status"], MemoryFormationStatus.COMPLETED.value)
        self.assertEqual(audit["accepted_count"], 1)

    async def test_explicit_attachment_forms_perceptual_memory_synchronously(
        self,
    ) -> None:
        asset = MemoryAsset(
            asset_id="asset-1",
            user_id="user-1",
            conversation_id="conversation-1",
            modality="text",
            file_name="report.txt",
            mime_type="text/plain",
            storage_uri="memory://asset-1",
            extracted_text="附件中的稳定文本",
            extraction_status="completed",
            index_status="pending",
        )
        repository = FakeFormationRepository(assets={asset.asset_id: asset})
        writer = FakeWriter()
        service = MemoryFormationService(
            repository=repository,
            governance=MemoryGovernance(repository),
            writer=writer,
        )

        result = await service.submit(
            _turn(
                input_text="请记住这个附件",
                asset_ids=[asset.asset_id],
            )
        )

        self.assertEqual(result.status, MemoryFormationStatus.COMPLETED)
        self.assertEqual(result.accepted_count, 1)
        self.assertEqual(
            writer.governed[0].candidate.memory_type,
            MemoryType.PERCEPTUAL,
        )
        self.assertEqual(
            [
                (source.source_type, source.source_id)
                for source in writer.governed[0].request.sources
            ],
            [("turn", "turn-1"), ("asset", "asset-1")],
        )

    async def test_ordinary_attachment_does_not_form_long_term_memory(self) -> None:
        """本轮使用附件不等于用户要求把附件保存为长期记忆。"""
        repository = FakeFormationRepository()
        writer = FakeWriter()
        service = MemoryFormationService(
            repository=repository,
            governance=MemoryGovernance(repository),
            writer=writer,
        )

        result = await service.submit(
            _turn(input_text="分析这个附件", asset_ids=["asset-1"])
        )

        self.assertEqual(result.status, MemoryFormationStatus.SKIPPED)
        self.assertEqual(writer.governed, [])

    async def test_explicit_text_memory_does_not_also_save_attached_file(self) -> None:
        """显式保存文本事实时，未被引用的同轮附件不能顺带成为记忆。"""
        asset = MemoryAsset(
            asset_id="asset-1",
            user_id="user-1",
            conversation_id="conversation-1",
            modality="text",
            file_name="report.txt",
            mime_type="text/plain",
            storage_uri="memory://asset-1",
            extracted_text="不应被顺带保存的附件文本",
            extraction_status="completed",
            index_status="pending",
        )
        repository = FakeFormationRepository(assets={asset.asset_id: asset})
        writer = FakeWriter()
        service = MemoryFormationService(
            repository=repository,
            governance=MemoryGovernance(repository),
            writer=writer,
        )

        result = await service.submit(
            _turn(
                input_text="请记住我的默认金额单位是元",
                asset_ids=[asset.asset_id],
            )
        )

        self.assertEqual(result.status, MemoryFormationStatus.COMPLETED)
        self.assertEqual(result.accepted_count, 1)
        self.assertEqual(
            [item.candidate.memory_type for item in writer.governed],
            [MemoryType.SEMANTIC],
        )

    async def test_one_candidate_failure_keeps_other_candidates(self) -> None:
        """一条候选异常只产生 partial，不回滚同批成功候选。"""

        class Extractor:
            async def extract(self, _turn, *, mode="automatic"):
                return [
                    MemoryCandidate(
                        memory_type=MemoryType.SEMANTIC,
                        content="候选一",
                        fact_key="test.one",
                    ),
                    MemoryCandidate(
                        memory_type=MemoryType.SEMANTIC,
                        content="候选二",
                        fact_key="test.two",
                    ),
                ]

        class Writer(FakeWriter):
            async def write(self, governed):
                if governed.candidate.fact_key == "test.one":
                    raise RuntimeError("单候选写入失败")
                return await super().write(governed)

        repository = FakeFormationRepository()
        writer = Writer()
        service = MemoryFormationService(
            repository=repository,
            governance=MemoryGovernance(repository),
            writer=writer,
            llm_extractor=Extractor(),
        )

        result = await service.submit(_turn(input_text="请记住这两项设置"))

        self.assertEqual(result.status, MemoryFormationStatus.PARTIAL)
        self.assertEqual(result.accepted_count, 1)
        self.assertEqual(result.failed_count, 1)
        self.assertEqual(len(writer.governed), 1)
        audit = repository.runs[result.formation_run_id]
        self.assertEqual(audit["status"], MemoryFormationStatus.PARTIAL.value)
        self.assertEqual(audit["failed_count"], 1)

    async def test_explicit_llm_extractor_supports_domain_fact_keys(self) -> None:
        """显式记忆字段由模型结构化，不受固定姓名或语言分支限制。"""

        class Llm:
            def __init__(self):
                self.prompt = ""

            async def ainvoke_auto(self, prompt):
                self.prompt = prompt
                return (
                    '{"candidates":[{'
                    '"memory_type":"semantic",'
                    '"content":"Olist 项目的销售额不包含运费",'
                    '"scope":"project",'
                    '"fact_key":"project.olist.metric.sales_amount.includes_freight",'
                    '"value":false,"confidence":0.99}]}'
                )

        llm = Llm()
        candidates = await LlmMemoryExtractor(llm).extract(
            _turn(
                input_text="请记住 Olist 项目的销售额不包含运费",
                assistant_content="不应进入显式记忆提取",
                project_id="olist",
            ),
            mode="explicit",
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0].fact_key,
            "project.olist.metric.sales_amount.includes_freight",
        )
        self.assertNotIn("不应进入显式记忆提取", llm.prompt)

    async def test_source_and_scope_are_rebuilt_from_server_identity(self) -> None:
        repository = FakeFormationRepository()
        governance = MemoryGovernance(repository)
        candidate = MemoryCandidate(
            memory_type=MemoryType.SEMANTIC,
            content="项目金额单位为元",
            scope=MemoryScope.PROJECT,
            fact_key="project.currency.unit",
        )

        with self.assertRaises(MemoryGovernanceError):
            await governance.govern(candidate, _turn())

        governed = await governance.govern(
            candidate,
            _turn(project_id="project-1"),
        )
        self.assertEqual(governed.request.user_id, "user-1")
        self.assertEqual(governed.request.project_id, "project-1")
        self.assertEqual(
            [(item.source_type, item.source_id) for item in governed.request.sources],
            [("turn", "turn-1")],
        )


class MemoryWritePolicyTest(unittest.TestCase):
    """验证三类长期记忆使用不同的逻辑写入身份。"""

    @staticmethod
    def _record(content: str, *, version: int = 1) -> MemoryRecord:
        return MemoryRecord(
            memory_id="old-memory",
            user_id="user-1",
            memory_type=MemoryType.SEMANTIC,
            scope=MemoryScope.USER,
            conversation_id=None,
            project_id=None,
            content=content,
            structured_data={"fact_key": "user.preference.language", "conditions": {}},
            status=MemoryStatus.ACTIVE,
            version=version,
            importance=0.8,
            confidence=1.0,
            supersedes_memory_id=None,
            expires_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    def test_semantic_identity_includes_conditions(self) -> None:
        request = MemoryCreate(
            user_id="user-1",
            memory_type=MemoryType.SEMANTIC,
            content="偏好中文",
            structured_data={
                "fact_key": "user.preference.language",
                "conditions": {"channel": "chat"},
            },
        )
        identity = resolve_memory_write_identity(request)
        self.assertEqual(identity.identity_type, "semantic_fact")
        self.assertEqual(identity.identity_key, "user.preference.language")
        self.assertEqual(identity.qualifiers["conditions"], {"channel": "chat"})

    def test_episodic_identity_is_event_key(self) -> None:
        request = MemoryCreate(
            user_id="user-1",
            memory_type=MemoryType.EPISODIC,
            content="任务完成",
            structured_data={"event_key": "analysis:2017-sales"},
        )
        identity = resolve_memory_write_identity(request)
        self.assertEqual(identity.identity_type, "episodic_event")
        self.assertEqual(identity.identity_key, "analysis:2017-sales")

    def test_perceptual_identity_is_asset_and_modality(self) -> None:
        request = MemoryCreate(
            user_id="user-1",
            memory_type=MemoryType.PERCEPTUAL,
            content="附件内容",
            structured_data={"asset_id": "asset-1", "modality": "text"},
        )
        identity = resolve_memory_write_identity(request)
        self.assertEqual(identity.identity_type, "perceptual_asset")
        self.assertEqual(identity.identity_key, "asset-1")
        self.assertEqual(identity.qualifiers["modality"], "text")

    def test_structured_change_is_not_treated_as_duplicate(self) -> None:
        """正文相同时，事实值或图关系变化仍必须创建新版本。"""
        current = SimpleNamespace(
            content="销售额口径包含运费",
            structured_data={
                "fact_key": "project.metric.sales.includes_freight",
                "value": True,
                "conditions": {},
                "entities": [],
            },
        )
        request = MemoryCreate(
            user_id="user-1",
            memory_type=MemoryType.SEMANTIC,
            content="销售额口径包含运费",
            structured_data={
                "fact_key": "project.metric.sales.includes_freight",
                "value": True,
                "conditions": {},
                "entities": [
                    {
                        "entity_id": "sales_amount",
                        "name": "销售额",
                        "entity_type": "metric",
                    }
                ],
            },
        )

        self.assertFalse(_same_payload(current, request))


class MemoryWriterTest(unittest.IsolatedAsyncioTestCase):
    """验证唯一写入口返回数据库决定，而不是本地猜测 duplicate。"""

    @staticmethod
    def _governed() -> GovernedCandidate:
        candidate = MemoryCandidate(
            memory_type=MemoryType.SEMANTIC,
            content="偏好中文",
            fact_key="user.preference.language",
        )
        request = MemoryCreate(
            user_id="user-1",
            memory_type=MemoryType.SEMANTIC,
            content="偏好中文",
            structured_data={"fact_key": "user.preference.language"},
        )
        return GovernedCandidate(candidate=candidate, request=request)

    @staticmethod
    def _record(
        content: str, *, version: int = 1, memory_id: str = "memory-1"
    ) -> MemoryRecord:
        return MemoryRecord(
            memory_id=memory_id,
            user_id="user-1",
            memory_type=MemoryType.SEMANTIC,
            scope=MemoryScope.USER,
            conversation_id=None,
            project_id=None,
            content=content,
            structured_data={"fact_key": "user.preference.language"},
            status=MemoryStatus.ACTIVE,
            version=version,
            importance=0.8,
            confidence=1.0,
            supersedes_memory_id=None,
            expires_at=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )

    async def test_writer_maps_managed_database_result(self) -> None:
        class Manager:
            async def add(self, _governed):
                return MemoryWriteResult(
                    action=MemoryDecisionAction.REPLACED,
                    record=MemoryWriterTest._record("偏好英文", version=2),
                    replaced_record=MemoryWriterTest._record(
                        "偏好中文", memory_id="old-memory"
                    ),
                )

        decision = await MemoryWriter(Manager()).write(self._governed())
        self.assertEqual(decision.action, MemoryDecisionAction.REPLACED)
        self.assertEqual(decision.memory_id, "memory-1")
        self.assertEqual(decision.replaced_memory_id, "old-memory")


class AgentServiceMemoryIntegrationTest(unittest.IsolatedAsyncioTestCase):
    """验证历史保存成功才允许进入 M3。"""

    class Conversation:
        def __init__(self, *, start_result: bool = True, finish_result: bool = True):
            self.start_result = start_result
            self.finish_result = finish_result

        async def start_turn(self, **_):
            return self.start_result

        async def finish_turn(self, **_):
            return self.finish_result

    class Formation:
        def __init__(self):
            self.turns: list[TurnMemoryInput] = []

        async def submit(self, turn: TurnMemoryInput):
            self.turns.append(turn)

    class Graph:
        async def ainvoke(self, *, input, **_):
            return {
                **input,
                "execution_mode": "daily_chat",
                "output_text": "你好",
                "llm_output": "你好",
            }

    def _service(self, conversation, formation):
        return AgentService(
            llm_client=object(),
            embedding_client=object(),
            dimension_value_search=object(),
            meta_tables_semantic_repository=object(),
            meta_columns_semantic_repository=object(),
            meta_metrics_semantic_repository=object(),
            meta_dimension_values_semantic_repository=object(),
            meta_catalog_repository=object(),
            dw_repository=object(),
            conversation_repository=conversation,
            graph=self.Graph(),
            memory_formation_service=formation,
        )

    async def test_memory_submission_uses_original_input_after_history_save(
        self,
    ) -> None:
        formation = self.Formation()
        result = await self._service(self.Conversation(), formation).arun(
            "请记住我叫张三", "conversation-1"
        )
        self.assertEqual(result["input_text"], "请记住我叫张三")
        self.assertEqual(len(formation.turns), 1)
        self.assertEqual(formation.turns[0].input_text, "请记住我叫张三")

    async def test_memory_submission_receives_unique_asset_ids(self) -> None:
        formation = self.Formation()
        await self._service(self.Conversation(), formation).arun(
            "分析附件",
            "conversation-1",
            asset_ids=["asset-1", "asset-1", "asset-2"],
        )
        self.assertEqual(formation.turns[0].asset_ids, ["asset-1", "asset-2"])

    async def test_history_failure_blocks_memory_submission(self) -> None:
        formation = self.Formation()
        await self._service(
            self.Conversation(start_result=False), formation
        ).arun("请记住我叫张三", "conversation-1")
        self.assertEqual(formation.turns, [])


if __name__ == "__main__":
    unittest.main()
