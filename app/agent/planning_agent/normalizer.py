"""把 Planner 草稿归一化为待提交的正式动作 DTO。"""

from app.agent.planning_agent.action_id import SequentialActionIdFactory
from app.agent.planning_agent.contracts import (
    ActionIssuanceContext,
    PlannerActionDraft,
)
from app.agent.state_result_store.contracts import AskUserRequest, NextAction, ToolCall


def normalize_action(
    draft: PlannerActionDraft,
    issuance: ActionIssuanceContext,
) -> NextAction:
    """由服务端生成序号和工具 action_id，模型不能覆盖。"""
    action_id = SequentialActionIdFactory().issue(
        run_id=issuance.run_id,
        iteration=issuance.iteration,
        action_seq=issuance.action_seq,
    )
    if draft.action_type.value == "tool_call":
        assert draft.tool_call is not None
        return NextAction(
            action_seq=issuance.action_seq,
            action_type=draft.action_type,
            tool_call=ToolCall(
                action_id=action_id,
                tool_name=draft.tool_call.tool_name,
                arguments=draft.tool_call.arguments,
                timeout_seconds=draft.tool_call.timeout_seconds,
            ),
            rationale_summary=draft.rationale_summary,
        )
    if draft.action_type.value == "ask_user":
        assert draft.ask_user is not None
        return NextAction(
            action_seq=issuance.action_seq,
            action_type=draft.action_type,
            ask_user=AskUserRequest(**draft.ask_user.model_dump()),
            rationale_summary=draft.rationale_summary,
        )
    return NextAction(
        action_seq=issuance.action_seq,
        action_type=draft.action_type,
        final_answer=draft.final_answer,
        rationale_summary=draft.rationale_summary,
    )


__all__ = ["normalize_action"]
