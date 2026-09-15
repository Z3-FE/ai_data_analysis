from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.agent.context_engine.compiler import ContextCompiler
from app.agent.context_engine.contracts import (
    ContextItem,
    ContextRequest,
    ContextSourceKind,
    ReferenceResolution,
)
from app.agent.context_engine.harness_context_contracts import RuntimeContext
from app.agent.context_engine.harness_context import (
    HarnessContextRequestFactory,
    HarnessRuntimeContextProjector,
)
from app.agent.state_result_store.state import new_harness_control_state


class Counter:
    def count_text(self, text: str) -> int:
        return len(text)

    def count_messages(self, messages) -> int:
        return sum(len(item["role"]) + len(item["content"]) for item in messages)

    def truncate(self, text: str, max_tokens: int, *, marker: str = "") -> str:
        return text[:max_tokens]

    def split_text(self, text: str, max_tokens: int) -> list[str]:
        return [text[index : index + max_tokens] for index in range(0, len(text), max_tokens)]


def state_with_harness(**updates):
    harness = new_harness_control_state(original_goal="分析销售数据")
    harness.update(updates)
    return {
        "input_text": "继续分析",
        "user_id": "user-1",
        "conversation_id": "conversation-1",
        "thread_id": "thread-1",
        "turn_id": "turn-1",
        "run_id": "run-1",
        "project_id": "project-1",
        "asset_ids": ["asset-1"],
        "harness": harness,
    }


def test_runtime_context_rejects_unknown_fields_and_has_isolated_defaults():
    with pytest.raises(ValidationError):
        RuntimeContext(original_goal="goal", user_id="spoofed")
    first = RuntimeContext(original_goal="goal")
    second = RuntimeContext(original_goal="goal")
    assert first.observations == second.observations == ()
    assert first.resolved_conditions == second.resolved_conditions == ()
    assert first.last_confirmation_answer is None


def test_projector_keeps_only_confirmed_scalar_conditions_and_bounded_observations():
    state = state_with_harness(
        resolved_conditions={
            "year": 2026,
            "user_id": "spoofed",
            "nested": {"rows": [1, 2]},
            "too_long": "x" * 1_001,
        },
        observations=[
            {
                "observation_id": "observation-1",
                "action_id": "action-1",
                "tool_name": "query_data",
                "status": "success",
                "summary": "查询完成",
                "result_ref": "artifact-1",
                "evidence_refs": ["evidence-1"],
                "limitations": [],
                "output_hash": "hash-1",
            }
        ],
        pending_confirmation={
            "confirmation_id": "confirmation-1",
            "question": "Which year?",
            "reason_code": "missing_condition",
        },
    )
    runtime = HarnessRuntimeContextProjector().project(state)
    assert [(item.key, item.value) for item in runtime.resolved_conditions] == [("year", "2026")]
    assert runtime.observations[0].result_ref == "artifact-1"
    assert not hasattr(runtime, "user_id")


def test_request_factory_maps_identity_and_project_scope_from_state():
    request = HarnessContextRequestFactory().create(
        state_with_harness(), system_instructions="你是数据助手"
    )
    assert request.user_id == "user-1"
    assert request.conversation_id == "conversation-1"
    assert request.project_id == "project-1"
    assert request.asset_ids == ("asset-1",)
    assert request.runtime_context is not None


def test_compiler_keeps_legacy_message_sequence_without_runtime_context():
    counter = Counter()
    compiler = ContextCompiler(counter)
    request = ContextRequest(
        user_id="user-1",
        conversation_id="conversation-1",
        query="当前问题",
        system_instructions="系统规则",
    )
    selected = (
        ContextItem(
            item_id="message-1",
            source_kind=ContextSourceKind.WORKING,
            content="历史消息",
            role="user",
            structured_data={"message_index": 1},
        ),
    )
    messages, _, tokens = compiler.compile(
        request=request, selected=selected, resolution=ReferenceResolution()
    )
    assert [item["content"] for item in messages] == ["系统规则", "历史消息", "当前问题"]
    assert tokens == compiler.base_token_count(request) + len("历史消息") + len("user")


def test_compiler_places_runtime_before_evidence_and_counts_it_in_base():
    counter = Counter()
    compiler = ContextCompiler(counter)
    runtime = RuntimeContext(original_goal="目标", resolved_conditions=[{"key": "year", "value": "2026"}])
    request = ContextRequest(
        user_id="user-1",
        conversation_id="conversation-1",
        query="当前问题",
        system_instructions="系统规则",
        runtime_context=runtime,
    )
    evidence = ContextItem(
        item_id="fact-1",
        source_kind=ContextSourceKind.SEMANTIC,
        content="证据",
    )
    messages, _, tokens = compiler.compile(
        request=request, selected=(evidence,), resolution=ReferenceResolution()
    )
    assert messages[0]["content"] == "系统规则"
    assert "Runtime Goal" in messages[1]["content"]
    assert "Known Facts and Rules" in messages[2]["content"]
    assert messages[-1]["content"] == "当前问题"
    assert compiler.base_token_count(request) == counter.count_messages(
        (messages[0], messages[1], messages[-1])
    )
    assert tokens == counter.count_messages(messages)


def test_projector_and_compiler_keep_free_text_confirmation_answer():
    state = state_with_harness(
        last_confirmation_answer="使用财务销售额，不包含运费，并考虑退款"
    )
    runtime = HarnessRuntimeContextProjector().project(state)
    assert runtime.last_confirmation_answer == "使用财务销售额，不包含运费，并考虑退款"

    request = HarnessContextRequestFactory().create(
        state, system_instructions="系统规则"
    )
    messages, _, _ = ContextCompiler(Counter()).compile(
        request=request, selected=(), resolution=ReferenceResolution()
    )
    assert "Last User Confirmation" in messages[1]["content"]
    assert "不包含运费" in messages[1]["content"]
