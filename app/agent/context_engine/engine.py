"""ContextEngine 完整构建编排。"""

import asyncio
import hashlib
import logging
import math
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from app.agent.context_engine.compiler import ContextCompiler
from app.agent.context_engine.contracts import (
    CompiledContext,
    ContextBuildTrace,
    ContextItem,
    ContextKnowledgeItem,
    ContextPolicy,
    ContextRequest,
    ContextRetrievalPlan,
    ContextSelectionDecision,
    ContextSourceKind,
    ContextSourceRef,
    ReferenceResolution,
)
from app.agent.context_engine.deduplicator import ContextDeduplicator
from app.agent.context_engine.history import ConversationHistoryManager
from app.repositories.context_repository import PostgresContextRepository
from app.agent.context_engine.interfaces import (
    ContextKnowledgeRetriever,
    ContextPlanner,
    MemoryContextReader,
    TokenCounter,
)
from app.agent.context_engine.resolver import ContextReferenceResolver
from app.agent.context_engine.selector import ContextSelectionResult, ContextSelector
from app.agent.memory.enums import MemoryType
from app.agent.memory.interfaces import MemoryRecord, MemorySearchResult, MemorySource

logger = logging.getLogger(__name__)


class ContextEngine:
    """从多种来源构建一次有预算、可追溯的模型上下文。"""

    _kind_by_memory_type = {
        MemoryType.SEMANTIC: ContextSourceKind.SEMANTIC,
        MemoryType.EPISODIC: ContextSourceKind.EPISODIC,
        MemoryType.PERCEPTUAL: ContextSourceKind.PERCEPTUAL,
    }
    _priority_by_kind = {
        ContextSourceKind.SEMANTIC: 72,
        ContextSourceKind.EPISODIC: 65,
        ContextSourceKind.PERCEPTUAL: 78,
        ContextSourceKind.RAG: 68,
    }

    def __init__(
        self,
        *,
        memory_reader: MemoryContextReader,
        context_repository: PostgresContextRepository,
        planner: ContextPlanner,
        resolver: ContextReferenceResolver,
        history: ConversationHistoryManager,
        deduplicator: ContextDeduplicator,
        selector: ContextSelector,
        compiler: ContextCompiler,
        token_counter: TokenCounter,
        policy: ContextPolicy,
        knowledge_retriever: ContextKnowledgeRetriever | None = None,
    ) -> None:
        self.memory_reader = memory_reader
        self.context_repository = context_repository
        self.planner = planner
        self.resolver = resolver
        self.history = history
        self.deduplicator = deduplicator
        self.selector = selector
        self.compiler = compiler
        self.token_counter = token_counter
        self.policy = policy
        self.knowledge_retriever = knowledge_retriever

    async def build(self, request: ContextRequest) -> CompiledContext:
        """执行 resolve、plan、gather、select、compile 和 trace 全流程。"""
        # ① 入参校验：身份与问题非空、预算为正、附件数量与长度上限，不合规直接 ValueError。
        self._validate_request(request)

        # ② 生成构建身份：build_id 随机；query_hash 是问题正文的 SHA256，审计可比对但不落正文。
        build_id = str(uuid4())
        query_hash = hashlib.sha256(request.query.encode("utf-8")).hexdigest()

        # ③ 预算兜底: token上限12000。
        token_budget = request.token_budget or self.policy.max_context_tokens

        # ④ 落"构建开始"审计：从此刻起 build_id 在库里可查；与上层 context.started 事件平行，这是落库 PG ：ContextBuildRunModel。
        await self.context_repository.start_build(
            build_id=build_id,
            request=request,
            token_budget=token_budget,
            query_hash=query_hash,
        )

        try:
            # ⑤ 委托 _build 全流程编排：resolve → plan → gather → select → compile。
            compiled = await self._build(
                build_id=build_id,
                query_hash=query_hash,
                token_budget=token_budget,
                request=request,
            )
            # ⑥ 成功终态：完整 trace 落库后返回编译结果。
            await self.context_repository.finish_build(compiled.trace)
            return compiled
        except Exception as exc:
            # ⑦ 失败也必须有终态：完整异常栈只进应用日志，审计只留类型摘要；
            #    fail_build 自身失败不能吞掉原始异常（见下方注释），最后原样上抛交上层补发 context.failed。
            logger.exception(
                "上下文构建失败：build_id=%s conversation_id=%s",
                build_id,
                request.conversation_id,
            )
            try:
                await self.context_repository.fail_build(
                    build_id,
                    error_message=(
                        f"{type(exc).__name__}: 上下文构建失败，"
                        "详细异常仅记录在应用日志"
                    ),
                )
            except Exception:
                # 保留原始构建异常，不能被审计更新异常覆盖。
                pass
            raise

    async def _build(
        self,
        *,
        build_id: str,
        query_hash: str,
        token_budget: int,
        request: ContextRequest,
    ) -> CompiledContext:
        # ① 基线复核：system_instructions 等固定开销先占预算，超了直接失败——召回只能花剩余空间。
        base_tokens = self.compiler.base_token_count(request)
        if base_tokens > token_budget:
            raise ValueError(
                "system_instructions 与当前问题已经超过本轮上下文 token 预算"
            )
        # ② 载入工作记忆：本会话近期消息，供引用解析与规划判断。
        working = await self.memory_reader.load_working(
            user_id=request.user_id,
            conversation_id=request.conversation_id,
            limit=None,
        )
        # ③ 解析引用并规划检索：resolver 把附件等指代落成 asset_ids；planner 决定本轮召回来什么——context.plan 事件与 trace.retrieval_plan 的源头。
        resolution = await self.resolver.resolve(request, working)
        plan = await self.planner.plan(request, working, resolution)

        # ④ 候选预算：总预算扣掉固定开销与格式保留；计划包含 working 且有空间时，压缩旧会话进候选（可能顺带滚动更新摘要）。
        candidate_budget = max(
            0,
            token_budget - base_tokens - self.policy.format_reserve_tokens,
        )
        history_result = None
        if plan.include_working and candidate_budget > 0:
            history_result = await self.history.prepare(
                user_id=request.user_id,
                conversation_id=request.conversation_id,
                query=plan.search_query,
                working=working,
                token_budget=int(candidate_budget * self.policy.working_history_ratio),
            )
        # ⑤ 并行召回其余来源：长期记忆（三类）、附件正文、可选 RAG。
        other_items = await self._gather_other_sources(
            request=request,
            plan=plan,
            resolution=resolution,
        )
        # ⑥ 合并候选并去重：历史条目与召回结果进同一池子，重复内容只留一份。
        candidates = [
            *(history_result.items if history_result is not None else ()),
            *other_items,
        ]
        deduplicated = self.deduplicator.deduplicate(candidates)
        # ⑦ 预算内打分选择：按相关性/重要性/新近度/优先级，花 candidate_budget 挑出入选项。
        selection = await self.selector.select(
            list(deduplicated.items), token_budget=candidate_budget
        )
        # ⑧ 只为入选项补真实记忆来源，落选项不白白查库。
        selected, source_decisions = await self._enrich_selected_sources(
            selection, request.user_id
        )
        selection = replace(
            selection,
            items=selected,
            decisions=source_decisions,
        )
        # ⑨ 真实 token 复核：编译后仍超预算就从最低分可选候选开始移除，循环到 fit——产出最终 messages/sections。
        selection, messages, sections, final_tokens = await self._fit_compiled_budget(
            request=request,
            resolution=resolution,
            selection=selection,
            token_budget=token_budget,
        )
        # ⑩ 汇总审计轨迹：去重决策 + 选择决策拼成完整 decisions，与 build() 的 build_id/query_hash 对齐审计开闸。
        decisions = (*deduplicated.decisions, *selection.decisions)
        trace = ContextBuildTrace(
            build_id=build_id,
            user_id=request.user_id,
            conversation_id=request.conversation_id,
            agent_type=request.agent_type,
            query_hash=query_hash,
            status="completed",
            token_budget=token_budget,
            final_token_count=final_tokens,
            candidate_count=len(candidates),
            selected_count=len(selection.items),
            # Trace 不复制检索问题正文，只保留本轮规划边界。
            retrieval_plan=self._trace_plan(plan),
            reference_resolution=resolution,
            decisions=decisions,
            summary_updated=bool(
                history_result is not None and history_result.summary_updated
            ),
        )
        # ⑪ 返回编译产物：messages 给 LLM、sections 给事件快照、trace 给审计与前端。
        return CompiledContext(
            build_id=build_id,
            messages=messages,
            sections=sections,
            token_count=final_tokens,
            resolved_asset_ids=resolution.asset_ids,
            trace=trace,
        )

    async def _gather_other_sources(
        self,
        *,
        request: ContextRequest,
        plan: ContextRetrievalPlan,
        resolution: ReferenceResolution,
    ) -> list[ContextItem]:
        """并行召回计划中的长期记忆、明确附件和可选 RAG。"""
        memory_calls = [
            self.memory_reader.search(
                memory_type=memory_type,
                user_id=request.user_id,
                query=plan.search_query,
                limit=self.policy.memory_recall_limit,
                conversation_id=request.conversation_id,
                project_id=request.project_id,
                asset_ids=(
                    list(resolution.asset_ids)
                    if memory_type is MemoryType.PERCEPTUAL and resolution.asset_ids
                    else None
                ),
            )
            for memory_type in plan.memory_types
            if memory_type in self._kind_by_memory_type
        ]
        memory_groups = await asyncio.gather(*memory_calls) if memory_calls else []
        items = [
            self._memory_item(result, resolution)
            for group in memory_groups
            for result in group
        ]
        assets, knowledge = await asyncio.gather(
            self._asset_items(request, resolution),
            self._knowledge_items(request, plan),
        )
        return [*items, *assets, *knowledge]

    def _memory_item(
        self, result: MemorySearchResult, resolution: ReferenceResolution
    ) -> ContextItem:
        memory = result.memory
        kind = self._kind_by_memory_type[memory.memory_type]
        asset_id = str(memory.structured_data.get("asset_id") or "")
        return ContextItem(
            item_id=memory.memory_id,
            source_kind=kind,
            content=memory.content,
            token_count=self.token_counter.count_text(memory.content),
            # Memory 内部已按类型融合检索信号，其最终分作为跨来源相关性输入。
            relevance=max(0.0, min(1.0, result.score)),
            importance=memory.importance,
            confidence=memory.confidence,
            recency=self._recency(memory),
            priority=self._priority_by_kind[kind],
            required=bool(asset_id and asset_id in resolution.asset_ids),
            structured_data={
                **memory.structured_data,
                "memory_id": memory.memory_id,
                "version": memory.version,
                "retrieval_source": result.source,
                "retrieval_signals": result.signals,
            },
            source_refs=(ContextSourceRef("memory", memory.memory_id),),
        )

    async def _asset_items(
        self, request: ContextRequest, resolution: ReferenceResolution
    ) -> list[ContextItem]:
        items = []
        for asset_id in resolution.asset_ids:
            asset = await self.memory_reader.get_asset(asset_id, request.user_id)
            if asset is None or not (asset.extracted_text or "").strip():
                continue
            content = (
                f"附件名称：{asset.file_name}\n"
                f"附件类型：{asset.modality}\n"
                f"提取内容：\n{asset.extracted_text}"
            )
            items.append(
                ContextItem(
                    item_id=f"asset:{asset.asset_id}",
                    source_kind=ContextSourceKind.PERCEPTUAL,
                    content=content,
                    token_count=self.token_counter.count_text(content),
                    relevance=1.0,
                    importance=0.8,
                    confidence=1.0,
                    recency=1.0,
                    priority=95,
                    required=True,
                    structured_data={
                        "asset_id": asset.asset_id,
                        "direct_asset": True,
                        "modality": asset.modality,
                        "file_name": asset.file_name,
                        "extraction_status": asset.extraction_status,
                    },
                    source_refs=(ContextSourceRef("asset", asset.asset_id),),
                )
            )
        return items

    async def _knowledge_items(
        self, request: ContextRequest, plan: ContextRetrievalPlan
    ) -> list[ContextItem]:
        if not plan.use_rag:
            return []
        if self.knowledge_retriever is None:
            raise RuntimeError("检索计划要求 RAG，但没有配置知识检索器")
        results = await self.knowledge_retriever.search(
            user_id=request.user_id,
            query=plan.search_query,
            limit=self.policy.memory_recall_limit,
            project_id=request.project_id,
        )
        return [self._knowledge_item(result) for result in results]

    def _knowledge_item(self, result: ContextKnowledgeItem) -> ContextItem:
        content = (
            f"{result.title}\n{result.content}" if result.title else result.content
        )
        return ContextItem(
            item_id=result.item_id,
            source_kind=ContextSourceKind.RAG,
            content=content,
            token_count=self.token_counter.count_text(content),
            relevance=result.relevance,
            importance=0.7,
            confidence=0.8,
            recency=0.5,
            priority=self._priority_by_kind[ContextSourceKind.RAG],
            structured_data=dict(result.metadata),
            source_refs=(ContextSourceRef("document", result.item_id),),
        )

    async def _enrich_selected_sources(
        self, selection: ContextSelectionResult, user_id: str
    ) -> tuple[tuple[ContextItem, ...], tuple[ContextSelectionDecision, ...]]:
        """只为最终选中的长期记忆读取真实来源，避免无效数据库查询。"""

        async def load_sources(item: ContextItem) -> list[MemorySource]:
            memory_id = str(item.structured_data.get("memory_id") or "")
            if not memory_id:
                return []
            return await self.memory_reader.get_sources(memory_id, user_id)

        source_groups = await asyncio.gather(
            *(load_sources(item) for item in selection.items)
        )
        selected: list[ContextItem] = []
        refs_by_id: dict[str, tuple[ContextSourceRef, ...]] = {}
        for item, sources in zip(selection.items, source_groups, strict=True):
            memory_id = str(item.structured_data.get("memory_id") or "")
            if not memory_id:
                selected.append(item)
                refs_by_id[item.item_id] = item.source_refs
                continue
            refs = tuple(
                dict.fromkeys(
                    (
                        *item.source_refs,
                        *(
                            ContextSourceRef(
                                source.source_type,
                                source.source_id,
                                source.source_path,
                            )
                            for source in sources
                        ),
                    )
                )
            )
            enriched = replace(item, source_refs=refs)
            selected.append(enriched)
            refs_by_id[item.item_id] = refs
        decisions = tuple(
            replace(
                decision,
                source_refs=refs_by_id.get(decision.item_id, decision.source_refs),
            )
            for decision in selection.decisions
        )
        return tuple(selected), decisions

    async def _fit_compiled_budget(
        self,
        *,
        request: ContextRequest,
        resolution: ReferenceResolution,
        selection: ContextSelectionResult,
        token_budget: int,
    ):
        """按真实消息 token 复核预算，必要时从最低分可选项开始移除。"""
        items = list(selection.items)
        decisions = list(selection.decisions)
        while True:
            messages, sections, tokens = self.compiler.compile(
                request=request, selected=tuple(items), resolution=resolution
            )
            if tokens <= token_budget:
                return (
                    replace(
                        selection,
                        items=tuple(items),
                        decisions=tuple(decisions),
                        used_tokens=sum(item.token_count for item in items),
                    ),
                    messages,
                    sections,
                    tokens,
                )
            removable = [item for item in items if not item.required]
            if not removable:
                raise ValueError("明确要求保留的上下文与当前问题超过本轮 token 预算")
            scores = {
                decision.item_id: decision.score
                for decision in decisions
                if decision.selected
            }
            victim = min(
                removable,
                key=lambda item: (scores.get(item.item_id, 0.0), -item.token_count),
            )
            items.remove(victim)
            decisions = [
                replace(
                    decision,
                    selected=False,
                    reason="编译协议开销导致超预算，移除最低分候选",
                    final_tokens=0,
                )
                if decision.item_id == victim.item_id and decision.selected
                else decision
                for decision in decisions
            ]

    @staticmethod
    def _recency(memory: MemoryRecord) -> float:
        updated = memory.updated_at
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=UTC)
        age_days = max(0.0, (datetime.now(UTC) - updated).total_seconds() / 86_400)
        half_life = {
            MemoryType.SEMANTIC: 3_650.0,
            MemoryType.EPISODIC: 90.0,
            MemoryType.PERCEPTUAL: 365.0,
        }[memory.memory_type]
        return math.pow(0.5, age_days / half_life)

    @staticmethod
    def _validate_request(request: ContextRequest) -> None:
        if not request.user_id.strip():
            raise ValueError("user_id 不能为空")
        if not request.conversation_id.strip():
            raise ValueError("conversation_id 不能为空")
        if not request.query.strip():
            raise ValueError("query 不能为空")
        if request.token_budget is not None and request.token_budget <= 0:
            raise ValueError("token_budget 必须大于 0")
        if len(request.asset_ids) > 32:
            raise ValueError("单次上下文构建最多允许 32 个附件 ID")
        if any(
            not asset_id.strip() or len(asset_id) > 128
            for asset_id in request.asset_ids
        ):
            raise ValueError("asset_ids 不能包含空值且长度不能超过 128")

    @staticmethod
    def _trace_plan(plan: ContextRetrievalPlan) -> ContextRetrievalPlan:
        """生成不含问题正文或模型自由文本的可审计计划。"""
        memory_types = ",".join(item.value for item in plan.memory_types) or "none"
        return replace(
            plan,
            search_query="",
            reason=(
                f"working={plan.include_working};"
                f"memory_types={memory_types};rag={plan.use_rag}"
            ),
        )


__all__ = ["ContextEngine"]
