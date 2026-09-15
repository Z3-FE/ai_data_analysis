"""Harness 终态的会话和记忆收口。"""

from __future__ import annotations

import logging

from app.agent.loop_controller.contracts import (
    FinalizationInput,
    FinalizationPort,
    FinalizationResult,
)
from app.agent.memory.contracts import TurnMemoryInput
from app.agent.memory.formation_service import MemoryFormationService
from app.agent.state_result_store.contracts import HarnessStatus
from app.repositories.conversation_repository import ConversationRepository

logger = logging.getLogger(__name__)


class PostgresFinalizationService(FinalizationPort):
    """把 Harness 最终答案写入会话，并在成功后提交记忆形成。"""

    def __init__(
        self,
        *,
        conversation_repository: ConversationRepository,
        memory_formation_service: MemoryFormationService | None = None,
    ) -> None:
        self.conversation_repository = conversation_repository
        self.memory_formation_service = memory_formation_service

    async def finalize(self, value: FinalizationInput) -> FinalizationResult:
        """结束同一 turn；暂停不会进入此方法。"""
        status = value.terminal_status.value
        history_saved = await self.conversation_repository.finish_turn(
            conversation_id=value.run_ref.conversation_id,
            user_id=value.run_ref.user_id,
            turn_id=value.run_ref.turn_id,
            execution_mode="harness",
            status=status,
            assistant_content=value.final_answer,
            output_type="text",
            output_payload={"message": value.final_answer},
            error_message=value.error_message,
        )
        if not history_saved:
            raise RuntimeError("Harness 最终答案未能写入会话历史")

        if (
            self.memory_formation_service is not None
            and value.terminal_status is HarnessStatus.COMPLETED
        ):
            try:
                await self.memory_formation_service.submit(
                    TurnMemoryInput(
                        user_id=value.run_ref.user_id,
                        conversation_id=value.run_ref.conversation_id,
                        turn_id=value.run_ref.turn_id,
                        run_id=value.run_ref.run_id,
                        input_text=value.user_query,
                        assistant_content=value.final_answer,
                        execution_mode="harness",
                        status=status,
                        output_type="text",
                        output_payload={"message": value.final_answer},
                    )
                )
            except Exception:
                # 记忆形成不能回滚已经返回给用户的最终答案。
                logger.exception(
                    "Harness 记忆形成提交失败：run_id=%s",
                    value.run_ref.run_id,
                )

        return FinalizationResult(
            run_ref=value.run_ref,
            status=value.terminal_status,
            final_answer=value.final_answer,
        )


__all__ = ["PostgresFinalizationService"]
