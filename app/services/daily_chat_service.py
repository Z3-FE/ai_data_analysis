"""独立日常聊天服务。

这个服务是 ContextEngine 的第一个真实应用适配层。它只读取会话消息、编译
日常聊天上下文并调用统一 LLM，不经过问题路由、SQL、Python 或报告节点。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from app.context_engine import (
    CompiledContext,
    ContextEngine,
    ContextItem,
    ContextPolicy,
    ContextRequest,
)
from app.repositories.conversation_repository import ConversationRepository

DEFAULT_HISTORY_LIMIT = 20
DEFAULT_CONTEXT_TOKEN_BUDGET = 4000
DAILY_CHAT_SYSTEM_INSTRUCTIONS = """
你是一个日常聊天助手。

请自然、准确、简洁地回答用户当前的问题。你可以参考提供的历史对话，但不要
虚构历史中没有的信息。当前模式只进行日常聊天，不调用 SQL、Python 或数据
分析流程；如果用户明确提出数据查询或分析需求，只需提醒他可以切换到对应的
数据能力，不要在这里自行执行数据分析。
""".strip()


class DailyChatService:
    """只负责日常聊天和本轮上下文生命周期。

    这个服务是业务层适配器：仓储负责读写会话，ContextEngine 负责编译模型
    上下文，AutoLLM 负责生成回答。本类不把日常聊天接入数据分析 Agent 图。
    """

    def __init__(
        self,
        llm_client: Any,
        conversation_repository: ConversationRepository,
        context_engine: ContextEngine | None = None,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
        context_token_budget: int = DEFAULT_CONTEXT_TOKEN_BUDGET,
    ) -> None:
        # 依赖通过构造函数注入，既能复用应用级客户端，也方便单元测试替换为
        # Fake Repository 和 Fake LLM，而不需要启动完整的 FastAPI 应用。
        if history_limit <= 0:
            raise ValueError("history_limit 必须大于 0")
        if context_token_budget <= 0:
            raise ValueError("context_token_budget 必须大于 0")
        self.llm_client = llm_client
        self.conversation_repository = conversation_repository
        self.context_engine = context_engine or ContextEngine()
        self.history_limit = history_limit
        self.context_token_budget = context_token_budget

    async def chat(
        self,
        input_text: str,
        conversation_id: str,
        user_id: str,
    ) -> dict[str, Any]:
        """执行一次独立的日常聊天，并保存本轮消息和上下文追踪。"""
        question = input_text.strip()
        if not question:
            raise ValueError("input_text 不能为空")
        if not conversation_id.strip():
            raise ValueError("conversation_id 不能为空")
        if not user_id.strip():
            raise ValueError("user_id 不能为空")

        # 一次用户提问对应一个 turn/run；thread_id 继续与 conversation_id 保持一致，
        # 这样后续接入其他执行模式时仍能沿用同一套会话身份约定。
        identity = self._new_identity(conversation_id)

        # 必须先读取已有消息，再保存当前问题。这样当前输入只作为本轮模型输入，
        # 不会同时被拼进历史上下文，避免模型看到重复的用户问题。
        history = await self.conversation_repository.get_recent_messages(
            user_id=user_id,
            conversation_id=conversation_id,
            limit=self.history_limit,
        )
        context_items = self._history_to_context_items(
            history,
            user_id=user_id,
            conversation_id=conversation_id,
        )

        # ContextRequest 描述本轮编译目标，ContextItem 则是从业务历史转换来的
        # 候选项；两者分离后，ContextEngine 不需要知道 PostgreSQL 的表结构。
        request = ContextRequest(
            current_input=question,
            token_budget=self.context_token_budget,
            user_id=user_id,
            conversation_id=conversation_id,
            agent_id="daily_chat",
            node_id="daily_chat",
        )

        # 日常聊天只允许使用 conversation_message。SQL、分析结果和执行日志即使
        # 将来进入同一套存储，也不能因为共享会话而自动进入普通聊天上下文。
        compiled = self.context_engine.compile(
            request=request,
            items=context_items,
            system_instructions=DAILY_CHAT_SYSTEM_INSTRUCTIONS,
            policy=ContextPolicy(
                allowed_source_types=frozenset({"conversation_message"}),
                section_order=("history",),
                section_titles={"history": "相关历史对话"},
            ),
        )

        # 追踪只记录本轮上下文的选择和预算信息，不把完整 Agent State 或大数据
        # 结果复制到 turn_outputs；需要调试时由 context-trace 接口按轮次读取。
        context_trace = self._context_trace(
            compiled,
            history_count=len(history),
        )

        # 用户消息和 running 轮次在调用 LLM 前落库，接口异常时也能留下失败轮次，
        # 前端刷新后不会把这次请求误认为从未发生。
        await self.conversation_repository.start_turn(
            conversation_id=conversation_id,
            user_id=user_id,
            thread_id=identity["thread_id"],
            turn_id=identity["turn_id"],
            run_id=identity["run_id"],
            input_text=question,
        )

        try:
            # 这是日常聊天链路唯一的模型调用；compiled.to_messages() 已经包含系统
            # 指令、被选中的历史上下文和当前问题。
            response = await self.llm_client.ainvoke_auto(compiled.to_messages())
            answer = response.content.strip()
            if not answer:
                raise ValueError("LLM 返回了空的日常聊天内容")
        except Exception as exc:
            # 失败时保存错误状态和上下文追踪，但不创建助手文本消息，避免把异常
            # 当成正常回答展示；异常继续抛给 API 层转换为 HTTP 错误。
            await self.conversation_repository.finish_turn(
                conversation_id=conversation_id,
                user_id=user_id,
                turn_id=identity["turn_id"],
                execution_mode="daily_chat",
                status="failed",
                assistant_content="",
                output_type="failure",
                output_payload={"message": str(exc)},
                context_trace=context_trace,
                error_message=str(exc),
            )
            raise

        # 成功时保存可直接展示的助手文本，并将 output_type 固定为 text。日常聊天
        # 不生成 query_result、rendered_report 或执行过程输出。
        await self.conversation_repository.finish_turn(
            conversation_id=conversation_id,
            user_id=user_id,
            turn_id=identity["turn_id"],
            execution_mode="daily_chat",
            status="completed",
            assistant_content=answer,
            output_type="text",
            output_payload={"message": answer},
            context_trace=context_trace,
        )

        # 返回值同时服务于 API 响应和前端调试；compiled_messages 便于确认本轮实际
        # 传给模型的内容，但数据库中的 context_trace 仍只保存轻量引用。
        return {
            **identity,
            "execution_mode": "daily_chat",
            "content": answer,
            "metadata": response.metadata,
            "context": {
                **context_trace,
                "compiled_messages": compiled.to_messages(),
            },
        }

    @staticmethod
    def _new_identity(conversation_id: str) -> dict[str, str]:
        """生成本轮身份；日常聊天暂不创建 LangGraph 运行。

        ``conversation_id`` 是跨轮稳定身份；``turn_id`` 和 ``run_id`` 只属于当前
        请求，用来关联本轮消息、输出和上下文追踪。
        """
        return {
            "conversation_id": conversation_id,
            "thread_id": conversation_id,
            "turn_id": str(uuid4()),
            "run_id": str(uuid4()),
        }

    @staticmethod
    def _history_to_context_items(
        messages: list[dict[str, Any]],
        user_id: str,
        conversation_id: str,
    ) -> list[ContextItem]:
        """将业务历史消息转换成通用 ContextItem。

        转换时保留角色、轮次和消息类型等元数据，同时把正文统一成“用户/助手”
        可读格式；ContextEngine 只处理转换后的通用对象。
        """
        items: list[ContextItem] = []
        for message in messages:
            role = str(message.get("role") or "unknown")
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            message_id = str(message.get("message_id") or message.get("id") or uuid4())
            created_at = message.get("created_at")
            if not isinstance(created_at, datetime):
                # 测试数据或旧记录缺少时间时仍允许参与编译；真实数据库记录会
                # 使用持久化的 created_at 参与新近性排序。
                created_at = datetime.utcnow()
            speaker = "用户" if role == "user" else "助手"
            items.append(
                ContextItem(
                    item_id=f"conversation-message:{message_id}",
                    content=f"{speaker}：{content}",
                    source_type="conversation_message",
                    source_ref=f"conversation_messages/{message_id}",
                    scope={
                        "user_id": user_id,
                        "conversation_id": conversation_id,
                    },
                    created_at=created_at,
                    importance=0.7 if role == "user" else 0.6,
                    section="history",
                    metadata={
                        "role": role,
                        "turn_id": message.get("turn_id"),
                        "message_type": message.get("message_type", "text"),
                    },
                )
            )
        return items

    @staticmethod
    def _context_trace(
        compiled: CompiledContext,
        history_count: int,
    ) -> dict[str, Any]:
        """生成适合持久化的上下文追踪，不保存完整业务 Agent State。

        ``selected_item_ids`` 和 ``selected_source_refs`` 用于回答“本轮用了什么
        历史”，``dropped_items`` 和 token 统计用于解释“为什么没有使用其他内容”。
        """
        return {
            "history_count": history_count,
            "selected_item_ids": [item.item_id for item in compiled.selected_items],
            "selected_source_refs": list(compiled.source_refs),
            "token_usage": dict(compiled.token_usage),
            "dropped_items": list(compiled.dropped_items),
            "compression_applied": compiled.compression_applied,
            "sections": [
                {
                    "name": section.name,
                    "title": section.title,
                    "item_ids": [item.item_id for item in section.items],
                }
                for section in compiled.sections
            ],
        }
