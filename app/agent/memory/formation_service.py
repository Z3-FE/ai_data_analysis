"""本轮完成后的长期记忆形成与治理编排。"""

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from uuid import uuid4

from app.agent.memory.contracts import (
    FormationEligibility,
    MemoryCandidate,
    MemoryDecision,
    MemoryFormationResult,
    TurnMemoryInput,
)
from app.agent.memory.eligibility import MemoryEligibilityEvaluator
from app.agent.memory.enums import (
    MemoryDecisionAction,
    MemoryFormationStatus,
    MemoryFormationTrigger,
)
from app.agent.memory.explicit_extractor import ExplicitMemoryExtractor
from app.agent.memory.governance import MemoryGovernance, MemoryGovernanceError
from app.agent.memory.interfaces import MemoryRepository
from app.agent.memory.llm_extractor import LlmMemoryExtractor
from app.agent.memory.perceptual_extractor import PerceptualMemoryExtractor
from app.agent.memory.writer import MemoryWriter

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """返回与 PostgreSQL TIMESTAMP 字段一致的无时区 UTC 时间。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MemoryFormationService:
    """统一执行 Eligibility、提取、治理、去重、写入和审计。"""

    def __init__(
        self,
        *,
        repository: MemoryRepository,
        governance: MemoryGovernance,
        writer: MemoryWriter,
        llm_extractor: LlmMemoryExtractor | None = None,
        eligibility: MemoryEligibilityEvaluator | None = None,
        explicit_extractor: ExplicitMemoryExtractor | None = None,
        perceptual_extractor: PerceptualMemoryExtractor | None = None,
        extractor_version: str = "m3-v1",
    ) -> None:
        # PostgreSQL 事实仓储，同时保存形成审计。
        self.repository = repository
        # 后端安全和来源治理，不信任提取器声明的身份。
        self.governance = governance
        # 唯一长期记忆写入口，内部只接受已经通过治理的候选。
        self.writer = writer
        # 自动形成使用 LLM；显式“记住”优先由 LLM 结构化，并保留确定性兜底。
        self.llm_extractor = llm_extractor
        self.eligibility = eligibility or MemoryEligibilityEvaluator(
            automatic_formation_enabled=llm_extractor is not None
        )
        self.explicit_extractor = explicit_extractor or ExplicitMemoryExtractor(
            memory_request_pattern=self.eligibility.policy.explicit_intent_pattern
        )
        self.perceptual_extractor = perceptual_extractor or PerceptualMemoryExtractor(
            repository
        )
        # 审计记录使用的提取器和提示词版本。
        self.extractor_version = extractor_version
        # 持有后台任务强引用，防止自动形成任务在完成前被回收。
        self._background_tasks: set[asyncio.Task[MemoryFormationResult]] = set()

    async def submit(self, turn: TurnMemoryInput) -> MemoryFormationResult:
        """提交一次形成任务；显式请求同步，自动候选后台执行。"""
        eligibility = self.eligibility.evaluate(turn)
        formation_run_id = str(uuid4())
        initial_status = (
            MemoryFormationStatus.PENDING
            if eligibility.eligible
            else MemoryFormationStatus.SKIPPED
        )
        await self.repository.create_formation_run(
            {
                "formation_run_id": formation_run_id,
                "user_id": turn.user_id,
                "conversation_id": turn.conversation_id,
                "turn_id": turn.turn_id,
                "run_id": turn.run_id,
                "trigger": eligibility.trigger.value,
                "status": initial_status.value,
                "extractor_version": self.extractor_version,
                "eligibility_reason": eligibility.reason,
                "completed_at": None if eligibility.eligible else _utcnow(),
            }
        )
        if not eligibility.eligible:
            return MemoryFormationResult(
                formation_run_id=formation_run_id,
                status=MemoryFormationStatus.SKIPPED,
                trigger=eligibility.trigger,
            )
        if eligibility.trigger is MemoryFormationTrigger.EXPLICIT:
            return await self.process(
                turn,
                formation_run_id=formation_run_id,
                eligibility=eligibility,
            )

        task = asyncio.create_task(
            self.process(
                turn,
                formation_run_id=formation_run_id,
                eligibility=eligibility,
            )
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._finish_background_task)
        return MemoryFormationResult(
            formation_run_id=formation_run_id,
            status=MemoryFormationStatus.PENDING,
            trigger=eligibility.trigger,
        )

    async def process(
        self,
        turn: TurnMemoryInput,
        *,
        formation_run_id: str | None = None,
        eligibility: FormationEligibility | None = None,
    ) -> MemoryFormationResult:
        """同步完成一次形成任务，供显式请求、后台任务和维护工具复用。"""
        resolved_eligibility = eligibility or self.eligibility.evaluate(turn)
        resolved_run_id = formation_run_id or str(uuid4())
        if formation_run_id is None:
            await self.repository.create_formation_run(
                {
                    "formation_run_id": resolved_run_id,
                    "user_id": turn.user_id,
                    "conversation_id": turn.conversation_id,
                    "turn_id": turn.turn_id,
                    "run_id": turn.run_id,
                    "trigger": resolved_eligibility.trigger.value,
                    "status": MemoryFormationStatus.PENDING.value,
                    "extractor_version": self.extractor_version,
                    "eligibility_reason": resolved_eligibility.reason,
                }
            )
        decisions: list[MemoryDecision] = []
        candidate_count = 0
        try:
            await self.repository.update_formation_run(
                resolved_run_id,
                status=MemoryFormationStatus.PROCESSING.value,
                attempts=1,
                started_at=_utcnow(),
            )
            candidates = await self._extract(turn, resolved_eligibility.trigger)
            candidate_count = len(candidates)
            for candidate in candidates:
                try:
                    governed = await self.governance.govern(candidate, turn)
                except MemoryGovernanceError as exc:
                    decisions.append(self._rejected(candidate, str(exc)))
                    continue
                except Exception as exc:
                    decisions.append(self._failed(candidate, str(exc)))
                    continue
                try:
                    decisions.append(await self.writer.write(governed))
                except Exception as exc:
                    decisions.append(self._failed(candidate, str(exc)))

            status = (
                MemoryFormationStatus.PARTIAL
                if any(
                    item.action is MemoryDecisionAction.FAILED
                    for item in decisions
                )
                else MemoryFormationStatus.COMPLETED
            )
            result = self._result(
                formation_run_id=resolved_run_id,
                trigger=resolved_eligibility.trigger,
                status=status,
                candidate_count=candidate_count,
                decisions=decisions,
            )
            await self.repository.update_formation_run(
                resolved_run_id,
                **self._audit_changes(result),
                completed_at=_utcnow(),
            )
            return result
        except Exception as exc:
            logger.exception(
                "长期记忆形成失败：formation_run_id=%s turn_id=%s",
                resolved_run_id,
                turn.turn_id,
            )
            result = self._result(
                formation_run_id=resolved_run_id,
                trigger=resolved_eligibility.trigger,
                status=MemoryFormationStatus.FAILED,
                candidate_count=candidate_count,
                decisions=decisions,
                error_message=str(exc),
            )
            await self.repository.update_formation_run(
                resolved_run_id,
                **self._audit_changes(result),
                completed_at=_utcnow(),
            )
            return result

    async def close(self) -> None:
        """应用关闭时等待已经提交的后台形成任务完成。"""
        if self._background_tasks:
            await asyncio.gather(*tuple(self._background_tasks), return_exceptions=True)

    def _finish_background_task(
        self, task: asyncio.Task[MemoryFormationResult]
    ) -> None:
        """回收后台任务，并读取异常避免静默留下 pending 审计。"""
        self._background_tasks.discard(task)
        if task.cancelled():
            logger.warning("长期记忆形成后台任务被取消")
            return
        try:
            task.result()
        except Exception:
            logger.exception("长期记忆形成后台任务异常退出")

    async def _extract(
        self, turn: TurnMemoryInput, trigger: MemoryFormationTrigger
    ) -> list[MemoryCandidate]:
        if trigger is MemoryFormationTrigger.EXPLICIT:
            candidates: list[MemoryCandidate] = []
            if self.llm_extractor is not None:
                try:
                    candidates = await self.llm_extractor.extract(
                        turn, mode="explicit"
                    )
                except Exception:
                    logger.exception(
                        "显式记忆结构化失败，使用确定性原文兜底：turn_id=%s",
                        turn.turn_id,
                    )
            if not candidates:
                candidates = self.explicit_extractor.extract(turn)
            # 仅“请记住这个附件”形成感知记忆；本轮带附件本身不是保存意图。
            if turn.asset_ids and self.explicit_extractor.references_attachment(
                turn.input_text
            ):
                candidates.extend(await self.perceptual_extractor.extract(turn))
            return candidates
        if trigger is MemoryFormationTrigger.AUTOMATIC:
            if self.llm_extractor is None:
                raise RuntimeError("自动记忆形成尚未配置 LLM 提取器。")
            return await self.llm_extractor.extract(turn, mode="automatic")
        return []

    @staticmethod
    def _rejected(candidate: MemoryCandidate, reason: str) -> MemoryDecision:
        return MemoryDecision(
            candidate_id=candidate.candidate_id,
            memory_type=candidate.memory_type,
            action=MemoryDecisionAction.REJECTED,
            reason=reason,
            fact_key=candidate.fact_key,
            content_hash=hashlib.sha256(
                candidate.content.strip().encode("utf-8")
            ).hexdigest(),
        )

    @staticmethod
    def _failed(candidate: MemoryCandidate, reason: str) -> MemoryDecision:
        """记录单条候选失败，允许同批其他候选继续处理。"""
        return MemoryDecision(
            candidate_id=candidate.candidate_id,
            memory_type=candidate.memory_type,
            action=MemoryDecisionAction.FAILED,
            reason=reason[:1000],
            fact_key=candidate.fact_key,
            content_hash=hashlib.sha256(
                candidate.content.strip().encode("utf-8")
            ).hexdigest(),
        )

    @staticmethod
    def _result(
        *,
        formation_run_id: str,
        trigger: MemoryFormationTrigger,
        status: MemoryFormationStatus,
        candidate_count: int,
        decisions: list[MemoryDecision],
        error_message: str = "",
    ) -> MemoryFormationResult:
        accepted_count = sum(
            item.action
            in {MemoryDecisionAction.CREATED, MemoryDecisionAction.REPLACED}
            for item in decisions
        )
        return MemoryFormationResult(
            formation_run_id=formation_run_id,
            status=status,
            trigger=trigger,
            candidate_count=candidate_count,
            accepted_count=accepted_count,
            rejected_count=sum(
                item.action is MemoryDecisionAction.REJECTED for item in decisions
            ),
            duplicate_count=sum(
                item.action is MemoryDecisionAction.DUPLICATE for item in decisions
            ),
            replaced_count=sum(
                item.action is MemoryDecisionAction.REPLACED for item in decisions
            ),
            failed_count=sum(
                item.action is MemoryDecisionAction.FAILED for item in decisions
            ),
            decisions=decisions,
            error_message=error_message,
        )

    @staticmethod
    def _audit_changes(result: MemoryFormationResult) -> dict:
        return {
            "status": result.status.value,
            "candidate_count": result.candidate_count,
            "accepted_count": result.accepted_count,
            "rejected_count": result.rejected_count,
            "duplicate_count": result.duplicate_count,
            "replaced_count": result.replaced_count,
            "failed_count": result.failed_count,
            "decisions": [
                item.model_dump(mode="json") for item in result.decisions
            ],
            "error_message": result.error_message,
        }


__all__ = ["MemoryFormationService"]
