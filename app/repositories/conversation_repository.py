"""Agent 会话历史仓储。

仓储只负责应用历史的读写，不参与 Agent 节点编排，也不读取 DW/Meta 数据。
"""

import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agent_history import (
    ConversationMessageModel,
    ConversationModel,
    ConversationTurnModel,
    TurnOutputModel,
)


def _json_safe(value: Any) -> Any:
    """把 Decimal 等数据库返回类型转换成 PostgreSQL JSONB 可保存的值。"""
    if value is None:
        return None
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


class ConversationRepository:
    """以短事务读写会话、轮次、消息和可渲染输出。"""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    @staticmethod
    def _conversation_dict(model: ConversationModel) -> dict[str, Any]:
        return {
            "conversation_id": model.conversation_id,
            "user_id": model.user_id,
            "thread_id": model.thread_id,
            "title": model.title,
            "data_source_id": model.data_source_id,
            "status": model.status,
            "active_run_id": model.active_run_id,
            "metadata": model.conversation_metadata or {},
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }

    @staticmethod
    def _turn_dict(model: ConversationTurnModel) -> dict[str, Any]:
        return {
            "turn_id": model.turn_id,
            "conversation_id": model.conversation_id,
            "user_id": model.user_id,
            "thread_id": model.thread_id,
            "run_id": model.run_id,
            "input_text": model.input_text,
            "execution_mode": model.execution_mode,
            "status": model.status,
            "error_message": model.error_message,
            "started_at": model.started_at,
            "completed_at": model.completed_at,
        }

    @staticmethod
    def _message_dict(model: ConversationMessageModel) -> dict[str, Any]:
        return {
            "id": model.message_id,
            "message_id": model.message_id,
            "conversation_id": model.conversation_id,
            "turn_id": model.turn_id,
            "role": model.role,
            "message_type": model.message_type,
            "sequence_no": model.sequence_no,
            "content": model.content,
            "metadata": model.message_metadata or {},
            "created_at": model.created_at,
        }

    @staticmethod
    def _output_dict(model: TurnOutputModel) -> dict[str, Any]:
        return {
            "output_id": model.output_id,
            "conversation_id": model.conversation_id,
            "turn_id": model.turn_id,
            "output_type": model.output_type,
            "payload": model.payload,
            "created_at": model.created_at,
            "updated_at": model.updated_at,
        }

    async def create_conversation(
        self,
        user_id: str,
        data_source_id: str = "olist",
        title: str = "新建会话",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """创建一个空会话，thread_id 与 conversation_id 保持一致。"""
        conversation_id = str(uuid4())
        model = ConversationModel(
            conversation_id=conversation_id,
            user_id=user_id,
            thread_id=conversation_id,
            title=title.strip() or "新建会话",
            data_source_id=data_source_id,
            conversation_metadata=_json_safe(metadata or {}),
        )
        async with self.session_factory() as session:
            session.add(model)
            await session.commit()
            await session.refresh(model)
        return self._conversation_dict(model)

    async def ensure_conversation(
        self,
        conversation_id: str,
        user_id: str,
        data_source_id: str = "olist",
    ) -> dict[str, Any]:
        """为直接调用 Agent 的场景补齐会话记录。"""
        async with self.session_factory() as session:
            model = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.conversation_id == conversation_id,
                    ConversationModel.user_id == user_id,
                )
            )
            if model is None:
                model = ConversationModel(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    thread_id=conversation_id,
                    title="新建会话",
                    data_source_id=data_source_id,
                )
                session.add(model)
                await session.commit()
                await session.refresh(model)
            return self._conversation_dict(model)

    async def start_turn(
        self,
        *,
        conversation_id: str,
        user_id: str,
        thread_id: str,
        turn_id: str,
        run_id: str,
        input_text: str,
    ) -> None:
        """以一个事务写入用户问题，并把会话置为 running。"""
        now = datetime.utcnow()
        async with self.session_factory() as session:
            conversation = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.conversation_id == conversation_id,
                    ConversationModel.user_id == user_id,
                )
            )
            if conversation is None:
                conversation = ConversationModel(
                    conversation_id=conversation_id,
                    user_id=user_id,
                    thread_id=thread_id,
                    title=input_text[:80] or "新建会话",
                    data_source_id="olist",
                )
                session.add(conversation)
            elif conversation.title == "新建会话" and input_text.strip():
                conversation.title = input_text.strip()[:80]

            turn = ConversationTurnModel(
                turn_id=turn_id,
                conversation_id=conversation_id,
                user_id=user_id,
                thread_id=thread_id,
                run_id=run_id,
                input_text=input_text,
                status="running",
                started_at=now,
            )
            user_message = ConversationMessageModel(
                message_id=str(uuid4()),
                conversation_id=conversation_id,
                turn_id=turn_id,
                user_id=user_id,
                role="user",
                message_type="text",
                sequence_no=0,
                content=input_text,
            )
            conversation.status = "running"
            conversation.active_run_id = run_id
            conversation.updated_at = now
            # 显式按外键依赖落库，避免新会话首次写入时消息先于父记录。
            session.add(conversation)
            await session.flush()
            session.add(turn)
            await session.flush()
            session.add(user_message)
            await session.commit()

    async def finish_turn(
        self,
        *,
        conversation_id: str,
        user_id: str,
        turn_id: str,
        execution_mode: str,
        status: str,
        assistant_content: str,
        output_type: str,
        output_payload: dict[str, Any],
        execution_trace: dict[str, Any] | None = None,
        error_message: str = "",
    ) -> None:
        """保存助手最终消息和受控结构化输出，并结束当前轮次。"""
        now = datetime.utcnow()
        async with self.session_factory() as session:
            turn = await session.scalar(
                select(ConversationTurnModel).where(
                    ConversationTurnModel.turn_id == turn_id,
                    ConversationTurnModel.conversation_id == conversation_id,
                    ConversationTurnModel.user_id == user_id,
                )
            )
            conversation = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.conversation_id == conversation_id,
                    ConversationModel.user_id == user_id,
                )
            )
            if turn is None or conversation is None:
                return

            turn.execution_mode = execution_mode
            turn.status = status
            turn.error_message = error_message or None
            turn.completed_at = now
            conversation.status = status
            conversation.active_run_id = None
            conversation.updated_at = now

            if assistant_content:
                assistant_message = ConversationMessageModel(
                    message_id=str(uuid4()),
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    user_id=user_id,
                    role="assistant",
                    message_type="text",
                    sequence_no=1,
                    content=assistant_content,
                    message_metadata={"status": status},
                )
                session.add(assistant_message)

            if output_type and output_payload:
                output = TurnOutputModel(
                    output_id=str(uuid4()),
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    output_type=output_type,
                    payload=_json_safe(output_payload),
                )
                session.add(output)

            if execution_trace is not None:
                trace_output = TurnOutputModel(
                    output_id=str(uuid4()),
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    output_type="execution_trace",
                    payload=_json_safe(execution_trace),
                )
                session.add(trace_output)
            await session.commit()

    async def list_conversations(
        self, user_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """按更新时间倒序返回当前用户的会话列表。"""
        async with self.session_factory() as session:
            models = list(
                (
                    await session.scalars(
                        select(ConversationModel)
                        .where(ConversationModel.user_id == user_id)
                        .order_by(desc(ConversationModel.updated_at))
                        .limit(limit)
                    )
                ).all()
            )
            result = [self._conversation_dict(model) for model in models]
            if not models:
                return result

            ids = [model.conversation_id for model in models]
            messages = (
                await session.scalars(
                    select(ConversationMessageModel)
                    .where(ConversationMessageModel.conversation_id.in_(ids))
                    .order_by(desc(ConversationMessageModel.created_at))
                )
            ).all()
            previews: dict[str, str] = {}
            for message in messages:
                if message.conversation_id not in previews:
                    previews[message.conversation_id] = message.content[:120]
            for item in result:
                item["last_message_preview"] = previews.get(item["conversation_id"])
            return result

    async def get_conversation(
        self, user_id: str, conversation_id: str
    ) -> dict[str, Any] | None:
        """返回会话详情、消息、轮次和可重渲染输出。"""
        async with self.session_factory() as session:
            conversation = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.conversation_id == conversation_id,
                    ConversationModel.user_id == user_id,
                )
            )
            if conversation is None:
                return None

            turns = (
                await session.scalars(
                    select(ConversationTurnModel)
                    .where(ConversationTurnModel.conversation_id == conversation_id)
                    .order_by(ConversationTurnModel.started_at)
                )
            ).all()
            messages = (
                await session.scalars(
                    select(ConversationMessageModel)
                    .where(ConversationMessageModel.conversation_id == conversation_id)
                    .order_by(
                        ConversationMessageModel.created_at,
                        ConversationMessageModel.sequence_no,
                    )
                )
            ).all()
            outputs = (
                await session.scalars(
                    select(TurnOutputModel)
                    .where(
                        TurnOutputModel.conversation_id == conversation_id,
                        TurnOutputModel.output_type != "execution_trace",
                    )
                    .order_by(TurnOutputModel.created_at)
                )
            ).all()
            return {
                "conversation": self._conversation_dict(conversation),
                "turns": [self._turn_dict(turn) for turn in turns],
                "messages": [self._message_dict(message) for message in messages],
                "outputs": [self._output_dict(output) for output in outputs],
            }

    async def get_execution_trace(
        self, user_id: str, conversation_id: str, turn_id: str
    ) -> dict[str, Any] | None:
        """读取指定会话轮次的压缩执行过程。"""
        async with self.session_factory() as session:
            output = await session.scalar(
                select(TurnOutputModel).where(
                    TurnOutputModel.conversation_id == conversation_id,
                    TurnOutputModel.turn_id == turn_id,
                    TurnOutputModel.output_type == "execution_trace",
                )
            )
            if output is None:
                return None

            turn = await session.scalar(
                select(ConversationTurnModel).where(
                    ConversationTurnModel.conversation_id == conversation_id,
                    ConversationTurnModel.turn_id == turn_id,
                    ConversationTurnModel.user_id == user_id,
                )
            )
            if turn is None:
                return None
            return self._output_dict(output)

    async def delete_conversation(self, user_id: str, conversation_id: str) -> bool:
        """删除当前用户的会话及其级联历史数据。"""
        async with self.session_factory() as session:
            conversation = await session.scalar(
                select(ConversationModel).where(
                    ConversationModel.conversation_id == conversation_id,
                    ConversationModel.user_id == user_id,
                )
            )
            if conversation is None:
                return False

            await session.execute(
                delete(TurnOutputModel).where(
                    TurnOutputModel.conversation_id == conversation_id
                )
            )
            await session.execute(
                delete(ConversationMessageModel).where(
                    ConversationMessageModel.conversation_id == conversation_id
                )
            )
            await session.execute(
                delete(ConversationTurnModel).where(
                    ConversationTurnModel.conversation_id == conversation_id
                )
            )
            await session.delete(conversation)
            await session.commit()
            return True
