import json
from datetime import UTC, datetime
from typing import get_type_hints

import pytest
from langgraph.graph.message import add_messages
from pydantic import ValidationError

from app.agent.state_result_store.contracts import (
    ActionType,
    AskUserRequest,
    ConfirmationReply,
    ConfirmationRequest,
    HarnessRequest,
    HarnessRunRef,
    HarnessStateSnapshot,
    HarnessStatusType,
    LoopPhaseStatusType,
    NextAction,
    ResultStatus,
    RunError,
    ToolCall,
    ToolResult,
)
from app.agent.state_result_store.state import (
    decode_harness_state,
    encode_harness_state,
    new_harness_control_state,
    restore_harness_state,
    transition_harness_state,
)
from app.agent.state import AgentState, HarnessGraphState


def run_ref():
    return HarnessRunRef(
        user_id="u1", conversation_id="c1", thread_id="c1", turn_id="t1", run_id="r1"
    )


def finalizing(intent="completed"):
    return transition_harness_state(
        new_harness_control_state(original_goal="goal"),
        status=HarnessStatusType.RUNNING,
        phase=LoopPhaseStatusType.FINALIZATION,
        terminal_intent=intent,
    )


def test_new_state_has_independent_mutable_values():
    first = new_harness_control_state(original_goal="goal")
    second = new_harness_control_state(original_goal="goal")
    first["observations"].append({"id": "one"})
    first["tool_retry_counts"]["a1"] = 1
    first["plan_progress"]["completed_steps"].append("done")
    first["resolved_conditions"]["year"] = "2026"
    assert second["observations"] == []
    assert second["tool_retry_counts"] == {}
    assert second["plan_progress"]["completed_steps"] == []
    assert second["resolved_conditions"] == {}
    assert first["status"] == "running"
    assert first["checkpoint_revision"] == 0


def test_request_new_and_restore_are_exclusive():
    assert HarnessRequest(mode="new", input_text="goal").run_ref is None
    assert HarnessRequest(mode="restore", run_ref=run_ref()).input_text is None
    invalid = [
        {"mode": "new"},
        {"mode": "new", "input_text": "goal", "run_ref": run_ref()},
        {"mode": "restore"},
        {"mode": "restore", "run_ref": run_ref(), "input_text": "changed"},
        {"mode": "restore", "run_ref": run_ref(), "project_id": "p1"},
        {"mode": "restore", "run_ref": run_ref(), "asset_ids": ("a1",)},
    ]
    for value in invalid:
        with pytest.raises(ValidationError):
            HarnessRequest(**value)


def test_valid_state_transitions_require_finalization_intent():
    state = new_harness_control_state(original_goal="goal")
    with pytest.raises(ValueError):
        transition_harness_state(
            state, status=HarnessStatusType.COMPLETED, phase=LoopPhaseStatusType.FINALIZATION
        )
    state = finalizing()
    assert state["status"] == "running"
    assert state["terminal_intent"] == "completed"
    state = transition_harness_state(
        state, status=HarnessStatusType.COMPLETED, phase=LoopPhaseStatusType.FINALIZATION
    )
    assert state["status"] == "completed"
    assert state["state_version"] == 2
    assert transition_harness_state(
        state, status=HarnessStatusType.COMPLETED, phase=LoopPhaseStatusType.FINALIZATION
    ) == state
    with pytest.raises(ValueError):
        transition_harness_state(
            state, status=HarnessStatusType.RUNNING, phase=LoopPhaseStatusType.PLAN
        )


def test_phase_cannot_skip_planning():
    with pytest.raises(ValueError):
        transition_harness_state(
            new_harness_control_state(original_goal="goal"),
            status=HarnessStatusType.RUNNING,
            phase=LoopPhaseStatusType.EXECUTE_TOOL,
        )


def test_waiting_confirmation_requires_matching_phase_and_request():
    confirmation = ConfirmationRequest(
        confirmation_id="c1", question="Which year?", reason_code="missing_condition"
    ).model_dump(mode="json")
    waiting = decode_harness_state({
        **new_harness_control_state(original_goal="goal"),
        "status": "waiting_confirmation",
        "phase": "wait_confirmation",
        "pending_confirmation": confirmation,
    })
    with pytest.raises(ValueError):
        restore_harness_state({**run_ref().model_dump(), "harness": waiting}, run_ref())
    resumed = transition_harness_state(
        waiting, status=HarnessStatusType.RUNNING, phase=LoopPhaseStatusType.RESTORE_RUN
    )
    assert resumed["pending_confirmation"] is None
    assert resumed["original_goal"] == "goal"
    for patch in ({"pending_confirmation": None}, {"phase": "plan"}):
        with pytest.raises(ValidationError):
            decode_harness_state({**waiting, **patch})


def test_checkpoint_round_trip_and_schema_boundary():
    state = new_harness_control_state(
        original_goal="goal", started_at="2026-09-11T00:00:00Z"
    )
    state["checkpoint_revision"] = 3
    assert decode_harness_state(json.loads(json.dumps(encode_harness_state(state)))) == state
    for patch in (
        {"schema_version": 2}, {"iteration": -1}, {"checkpoint_revision": -1},
        {"unexpected": "value"}, {"original_goal": ""}, {"max_iterations": 0},
    ):
        with pytest.raises(ValidationError):
            decode_harness_state({**state, **patch})


@pytest.mark.parametrize("phase", [LoopPhaseStatusType.PLAN, LoopPhaseStatusType.FINALIZATION])
def test_restore_preserves_identity_and_scene(phase):
    harness = finalizing() if phase is LoopPhaseStatusType.FINALIZATION else {
        **new_harness_control_state(original_goal="goal"), "phase": phase.value
    }
    state = {**run_ref().model_dump(), "harness": harness, "sql": "select 1"}
    assert restore_harness_state(state, run_ref()) == state
    for field in run_ref().model_dump():
        with pytest.raises(ValueError):
            restore_harness_state({**state, field: "wrong"}, run_ref())


def test_restore_rejects_completed_run():
    state = transition_harness_state(
        finalizing(), status=HarnessStatusType.COMPLETED, phase=LoopPhaseStatusType.FINALIZATION
    )
    with pytest.raises(ValueError):
        restore_harness_state({**run_ref().model_dump(), "harness": state}, run_ref())


def test_graph_state_keeps_old_fields_and_messages_reducer():
    old = get_type_hints(AgentState, include_extras=True)
    combined = get_type_hints(HarnessGraphState, include_extras=True)
    assert old.keys() <= combined.keys()
    assert "project_id" in combined
    assert combined["messages"].__metadata__ == (add_messages,)
    assert "sql_result" in combined


def test_next_action_requires_exact_payload():
    call = ToolCall(action_id="a1", tool_name="query_data")
    ask = AskUserRequest(question="Which year?", reason_code="missing_condition")
    for kind, payload in (
        (ActionType.TOOL_CALL, {"tool_call": call}),
        (ActionType.ASK_USER, {"ask_user": ask}),
        (ActionType.FINAL_ANSWER, {"final_answer": "answer"}),
    ):
        assert NextAction(action_seq=1, action_type=kind, **payload).action_seq == 1
    for payload in (
        {}, {"tool_call": call, "final_answer": "answer"},
        {"final_answer": "answer"}, {"ask_user": {}},
    ):
        with pytest.raises(ValidationError):
            NextAction(action_seq=1, action_type=ActionType.TOOL_CALL, **payload)


def test_tool_result_is_json_serializable_and_consistent():
    now = datetime.now(UTC)
    values = dict(
        tool_call_id="a1", tool_name="query_data", status=ResultStatus.SUCCESS,
        summary="one row", started_at=now, finished_at=now, duration_ms=0,
    )
    result = ToolResult(**values)
    assert result.model_dump(mode="json")["status"] == "success"
    for patch in (
        {"error_code": "failed"}, {"retryable": True},
        {"status": ResultStatus.NEEDS_USER}, {"output": {"rows": [1]}},
    ):
        with pytest.raises(ValidationError):
            ToolResult(**{**values, **patch})


def test_confirmation_and_error_validation():
    with pytest.raises(ValidationError):
        RunError(category="tool", code="", message="failed")
    with pytest.raises(ValidationError):
        ConfirmationReply(
            confirmation_id="c1", answer="no", decision="reject",
            resolved_conditions={"year": "2026"},
        )
    with pytest.raises(ValidationError):
        AskUserRequest(
            question="Which year?", reason_code="missing_condition",
            required_fields=("year", "year"),
        )
    with pytest.raises(ValidationError):
        HarnessStateSnapshot(original_goal="goal", status="completed", phase="plan")
