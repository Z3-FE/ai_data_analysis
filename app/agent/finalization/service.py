"""Harness 终态的会话历史、最终检查点、运行释放与记忆形成收口。"""

from __future__ import annotations

import hashlib
import json
import logging

from app.agent.finalization.errors import FinalizationFailure
from app.agent.loop_controller.contracts import (
    FinalizationInput,
    FinalizationPort,
    FinalizationResult,
    HarnessRunStore,
)
from app.agent.memory.contracts import TurnMemoryInput
from app.agent.memory.formation_service import MemoryFormationService
from app.agent.state import HarnessGraphState
from app.agent.state_result_store.contracts import (
    HarnessRunRef,
    HarnessStateSnapshot,
    HarnessStatusType,
    LoopPhaseStatusType,
)
from app.agent.state_result_store.state import transition_harness_state
from app.agent.tool_runtime.artifacts import ArtifactReadRequest, ResultArtifactStore
from app.models.harness import HarnessFinalizationModel
from app.repositories.conversation_repository import ConversationRepository
from app.repositories.finalization_repository import (
    FinalizationLedgerError,
    PostgresFinalizationLedger,
    stage_rank,
)

logger = logging.getLogger(__name__)


class PostgresFinalizationService(FinalizationPort):
    """按 Ledger 固定顺序完成收口，并提供崩溃后的对账恢复。

    固定顺序：锁定收口内容 → 保存会话历史 → 写入终态 checkpoint →
    条件释放 active_run → 提交记忆形成 → 账本完成。每一步操作本身
    幂等，Ledger 只负责"进行到哪一步"；任何一步失败都保持运行现场，
    reconcile 从 Ledger 当前阶段继续，不重新调用 Planner 或工具。
    """

    def __init__(
        self,
        *,
        conversation_repository: ConversationRepository,
        ledger: PostgresFinalizationLedger,
        run_store: HarnessRunStore,
        memory_formation_service: MemoryFormationService | None = None,
        artifact_store: ResultArtifactStore | None = None,
    ) -> None:
        self.conversation_repository = conversation_repository
        self.ledger = ledger
        self.run_store = run_store
        self.memory_formation_service = memory_formation_service
        # 结构化输出只以引用进入收口；完整内容在这里按引用取回。
        self.artifact_store = artifact_store

    async def finalize(self, value: FinalizationInput) -> FinalizationResult:
        """结束同一 turn；暂停不会进入此方法。"""
        state = await self.run_store.load(value.run_ref)
        self._require_finalizable(state, value.terminal_status)
        model = await self.ledger.prepare(value, self._digest(value))
        return await self._drive(model, value, state)

    async def reconcile(self, *, run_id: str, user_id: str) -> FinalizationResult:
        """从 Ledger 当前阶段恢复一次未完成的收口。"""
        model = await self.ledger.get(run_id=run_id, user_id=user_id)
        if model is None:
            raise FinalizationLedgerError(
                f"该运行没有收口账本，无需对账: run_id={run_id}"
            )
        # 输入来自锁定时的 input_payload，结构上不可能回到 Planner 或工具。
        value = FinalizationInput.model_validate(model.input_payload)
        await self.ledger.mark_reconcile_attempt(
            run_id=model.run_id, user_id=model.user_id
        )
        state = await self.run_store.load(value.run_ref)
        return await self._drive(model, value, state)

    async def _drive(
        self,
        model: HarnessFinalizationModel,
        value: FinalizationInput,
        state: HarnessGraphState,
    ) -> FinalizationResult:
        """从账本当前阶段推进到 completed；失败时停在已到达的阶段。"""
        try:
            if self._before(model, "history_saved"):
                await self._save_history(value)
                model = await self.ledger.advance(
                    run_id=model.run_id, user_id=model.user_id, stage="history_saved"
                )
            if self._before(model, "checkpoint_saved"):
                state["harness"] = transition_harness_state(
                    state["harness"],
                    status=value.terminal_status,
                    phase=LoopPhaseStatusType.FINALIZATION,
                    terminal_intent=value.terminal_status.value,
                )
                await self.run_store.save(value.run_ref, state)
                model = await self.ledger.advance(
                    run_id=model.run_id, user_id=model.user_id, stage="checkpoint_saved"
                )
            if self._before(model, "released"):
                released = await self.conversation_repository.release_active_run(
                    conversation_id=value.run_ref.conversation_id,
                    user_id=value.run_ref.user_id,
                    run_id=value.run_ref.run_id,
                    status=value.terminal_status.value,
                )
                if not released:
                    raise RuntimeError("会话不存在，无法释放 active_run")
                model = await self.ledger.advance(
                    run_id=model.run_id, user_id=model.user_id, stage="released"
                )
            if self._before(model, "formation_submitted"):
                formation_run_id = await self._submit_formation(value)
                model = await self.ledger.advance(
                    run_id=model.run_id,
                    user_id=model.user_id,
                    stage="formation_submitted",
                    formation_run_id=formation_run_id,
                )
            if self._before(model, "completed"):
                model = await self.ledger.advance(
                    run_id=model.run_id, user_id=model.user_id, stage="completed"
                )
        except Exception as exc:
            raise FinalizationFailure(value.run_ref, model.stage) from exc
        snapshot = HarnessStateSnapshot.model_validate(state["harness"])
        return FinalizationResult(
            run_ref=value.run_ref,
            status=value.terminal_status,
            final_answer=value.final_answer,
            final_output_type=value.final_output_type,
            final_output_ref=value.final_output_ref,
            iteration=snapshot.iteration,
            last_error=snapshot.last_error,
        )

    async def _save_history(self, value: FinalizationInput) -> None:
        """保存受控 assistant message 和 turn output。"""
        output_type, output_payload = await self._final_output(value)
        saved = await self.conversation_repository.finish_turn(
            conversation_id=value.run_ref.conversation_id,
            user_id=value.run_ref.user_id,
            turn_id=value.run_ref.turn_id,
            execution_mode="harness",
            status=value.terminal_status.value,
            assistant_content=value.final_answer,
            output_type=output_type,
            output_payload=output_payload,
            error_message=value.error_message,
        )
        if not saved:
            raise RuntimeError("Harness 最终答案未能写入会话历史")

    async def _final_output(
        self, value: FinalizationInput
    ) -> tuple[str, dict[str, object]]:
        """没有结构化输出引用时只保存文字结果。"""
        if value.final_output_ref is None:
            return "text", {"message": value.final_answer}
        if self.artifact_store is None:
            raise RuntimeError("结构化输出需要配置 ArtifactStore 才能收口")
        record = await self.artifact_store.read(
            ArtifactReadRequest(
                run_ref=value.run_ref,
                result_ref=value.final_output_ref,
            )
        )
        payload = record.payload.get(value.final_output_type)
        if not isinstance(payload, dict) or not payload:
            raise RuntimeError(
                f"结果引用中没有可用的 {value.final_output_type} 内容"
            )
        return value.final_output_type, payload

    async def _submit_formation(self, value: FinalizationInput) -> str | None:
        """提交记忆形成并返回 formation_run_id；非完成终态没有形成任务。"""
        if (
            self.memory_formation_service is None
            or value.terminal_status is not HarnessStatusType.COMPLETED
        ):
            return None
        result = await self.memory_formation_service.submit(
            TurnMemoryInput(
                user_id=value.run_ref.user_id,
                conversation_id=value.run_ref.conversation_id,
                turn_id=value.run_ref.turn_id,
                run_id=value.run_ref.run_id,
                input_text=value.user_query,
                assistant_content=value.final_answer,
                execution_mode="harness",
                status=value.terminal_status.value,
                output_type=value.final_output_type,
                output_payload={"message": value.final_answer},
                asset_ids=list(value.asset_ids),
            )
        )
        return result.formation_run_id

    @staticmethod
    def _require_finalizable(
        state: HarnessGraphState, terminal_status: HarnessStatusType
    ) -> None:
        """收口前校验现场：必须处于 finalization 阶段且终态意图一致。"""
        snapshot = HarnessStateSnapshot.model_validate(state["harness"])
        if snapshot.phase is not LoopPhaseStatusType.FINALIZATION:
            raise ValueError(
                f"现场不在 finalization 阶段，不能收口: {snapshot.phase.value}"
            )
        if snapshot.status is HarnessStatusType.RUNNING:
            if snapshot.terminal_intent != terminal_status.value:
                raise ValueError("现场 terminal_intent 与收口终态不一致")
        elif snapshot.status is not terminal_status:
            raise ValueError("现场终态与收口终态不一致")

    @staticmethod
    def _before(model: HarnessFinalizationModel, stage: str) -> bool:
        """账本是否尚未推进到目标阶段。"""
        return stage_rank(model.stage) < stage_rank(stage)

    @staticmethod
    def _digest(value: FinalizationInput) -> str:
        """对锁定的收口内容生成稳定摘要；不同内容不允许复用同一账本行。"""
        payload = json.dumps(
            value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = ["FinalizationFailure", "PostgresFinalizationService"]
