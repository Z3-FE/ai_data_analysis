"""所有成功路由共享的 Working Memory 收尾节点。"""

from langchain_core.messages import AIMessage, HumanMessage

from app.agent.state import AgentState
from app.agent.turn_output import build_turn_output


async def finalize_turn(state: AgentState) -> dict:
    """把本轮用户消息和助手可见摘要追加到 Checkpointer 状态。"""
    question = str(state.get("input_text") or "").strip()
    output_type, _, assistant_content = build_turn_output(state)
    messages = []
    # 使用绝对序号保存消息位置；未来即使裁剪 Checkpointer 中的旧消息，摘要覆盖范围仍稳定。
    next_message_index = len(state.get("messages", []))
    if question:
        messages.append(
            HumanMessage(
                content=question,
                additional_kwargs={
                    "turn_id": state.get("turn_id", ""),
                    "message_index": next_message_index,
                    # 历史附件引用必须保留稳定 ID，不能依赖前端文件名猜测。
                    "asset_ids": list(dict.fromkeys(state.get("asset_ids", []))),
                },
            )
        )
    if assistant_content:
        messages.append(
            AIMessage(
                content=assistant_content,
                additional_kwargs={
                    "turn_id": state.get("turn_id", ""),
                    "message_index": next_message_index + int(bool(question)),
                    "execution_mode": state.get("execution_mode", ""),
                    "output_type": output_type,
                },
            )
        )
    return {"messages": messages} if messages else {}


__all__ = ["finalize_turn"]
