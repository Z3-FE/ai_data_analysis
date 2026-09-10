from pydantic import ValidationError
import pytest

from app.agent.harness.contracts import (
    ActionType,
    HarnessStatus,
    LoopPhase,
    NextAction,
    ResultStatus,
    RunError,
    ToolCall,
    ToolResult,
)
from app.agent.harness.state import (
    new_harness_control_state,
    transition_harness_state,
)


def test_new_state_has_independent_mutable_values():
    first = new_harness_control_state(original_goal="goal")
    second = new_harness_control_state()
    first["observations"].append({"id": "one"})
    assert second["observations"] == []
    assert first["status"] == "running"


def test_valid_state_transitions():
    state = new_harness_control_state()
    state = transition_harness_state(
        state, status=HarnessStatus.COMPLETED, phase=LoopPhase.FINALIZATION
    )
    assert state["status"] == "completed"
    assert state["phase"] == "finalization"


def test_terminal_state_cannot_resume():
    state = new_harness_control_state()
    state = transition_harness_state(
        state, status=HarnessStatus.FAILED, phase=LoopPhase.FINALIZATION
    )
    with pytest.raises(ValueError):
        transition_harness_state(
            state, status=HarnessStatus.RUNNING, phase=LoopPhase.PLAN
        )


def test_next_action_requires_exact_payload():
    action = NextAction(
        action_type=ActionType.TOOL_CALL,
        tool_call=ToolCall(action_id="a1", tool_name="query_data"),
    )
    assert action.tool_call is not None
    with pytest.raises(ValidationError):
        NextAction(action_type=ActionType.TOOL_CALL, final_answer="answer")
    with pytest.raises(ValidationError):
        NextAction(action_type=ActionType.FINAL_ANSWER)


def test_tool_result_is_json_serializable():
    result = ToolResult(
        action_id="a1",
        tool_name="query_data",
        status=ResultStatus.SUCCESS,
        output={"row_count": 1},
        error=None,
    )
    assert result.model_dump(mode="json")["status"] == "success"


def test_run_error_requires_non_empty_code_and_message():
    with pytest.raises(ValidationError):
        RunError(category="tool", code="", message="failed")
