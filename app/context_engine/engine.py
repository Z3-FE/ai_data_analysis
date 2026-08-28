"""通用 ContextEngine。

ContextEngine 不认识具体业务对象，只处理 ContextItem。业务 Agent 负责把会话、
文档、工具结果或分析证据转换成 ContextItem，再交给本类完成上下文编译。
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from .models import (
    CompiledContext,
    ContextItem,
    ContextPolicy,
    ContextRequest,
    ContextSection,
    ContextTrace,
)

TokenCounter = Callable[[str], int]

_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9_]+")


class ContextEngine:
    """收集、隔离、选择、组织、压缩并追踪上下文。"""

    def __init__(self, token_counter: TokenCounter | None = None) -> None:
        """
        Args:
            token_counter: 可选的真实 Token 计算器。未提供时使用无依赖估算器；
                未来某个 Agent 可以注入与其模型匹配的 tokenizer。
        """
        self._token_counter = token_counter or estimate_tokens

    def gather(
        self, items: Iterable[ContextItem | Mapping[str, Any]]
    ) -> list[ContextItem]:
        """标准化候选项并按 item_id 去重。"""
        return self._gather_detailed(items).items

    def _gather_detailed(
        self, items: Iterable[ContextItem | Mapping[str, Any]]
    ) -> _GatherResult:
        gathered: list[ContextItem] = []
        seen: set[str] = set()
        dropped: list[dict[str, Any]] = []
        candidate_count = 0
        for raw_item in items:
            candidate_count += 1
            item = self._normalize_item(raw_item)
            if item.item_id in seen:
                dropped.append(
                    {
                        "item_id": item.item_id,
                        "source_ref": item.source_ref,
                        "reason": "duplicate_item_id",
                    }
                )
                continue
            seen.add(item.item_id)
            if item.token_count == 0 and item.content:
                item = replace(item, token_count=self._count_tokens(item.content))
            gathered.append(item)
        return _GatherResult(
            items=gathered,
            dropped_items=dropped,
            candidate_count=candidate_count,
        )

    def isolate(
        self,
        items: Iterable[ContextItem],
        request: ContextRequest,
        policy: ContextPolicy | None = None,
    ) -> list[ContextItem]:
        """按照作用域和允许的数据源隔离候选上下文。"""
        policy = policy or ContextPolicy()
        request_scope = request.effective_scope()
        isolated: list[ContextItem] = []
        for item in items:
            if (
                policy.allowed_source_types is not None
                and item.source_type not in policy.allowed_source_types
            ):
                continue
            if any(
                request_scope.get(key) != value for key, value in item.scope.items()
            ):
                continue
            isolated.append(item)
        return isolated

    def select(
        self,
        items: Iterable[ContextItem],
        request: ContextRequest,
        policy: ContextPolicy | None = None,
    ) -> list[ContextItem]:
        """按相关性、新近性、重要性和 Token 预算选择候选项。"""
        policy = policy or ContextPolicy()
        return self._select_detailed(list(items), request, policy).items

    def structure(
        self,
        items: Iterable[ContextItem],
        policy: ContextPolicy | None = None,
    ) -> list[ContextSection]:
        """将选中的上下文按 section 分组，生成稳定的结构化结果。"""
        policy = policy or ContextPolicy()
        groups: dict[str, list[ContextItem]] = defaultdict(list)
        for item in items:
            groups[item.section].append(item)

        section_names = list(policy.section_order)
        section_names.extend(name for name in groups if name not in section_names)
        sections: list[ContextSection] = []
        for name in section_names:
            section_items = groups.get(name, [])
            if not section_items:
                continue
            title = policy.section_titles.get(name, name)
            body = "\n".join(self._format_item(item) for item in section_items)
            content = f"[{title}]\n{body}"
            sections.append(
                ContextSection(
                    name=name,
                    title=title,
                    items=section_items,
                    content=content,
                    token_count=self._count_tokens(content),
                )
            )
        return sections

    def compress(
        self,
        items: Iterable[ContextItem],
        token_budget: int,
        *,
        compression_marker: str = "[内容已压缩]",
    ) -> list[ContextItem]:
        """在预算内压缩候选项；必要时截断末尾内容或丢弃项目。"""
        compressed, _, _ = self._compress_detailed(
            list(items), token_budget, compression_marker
        )
        return compressed

    def compile(
        self,
        request: ContextRequest,
        items: Iterable[ContextItem | Mapping[str, Any]],
        *,
        system_instructions: str | None = None,
        policy: ContextPolicy | None = None,
    ) -> CompiledContext:
        """执行完整的上下文编译流程。

        流程为 Gather -> Isolate -> Select -> Compress -> Structure -> Trace。
        system_instructions 和 current_input 不作为候选项参与排序，
        但会被放入最终的模型消息中。token_budget 仅约束选中的上下文项，
        调用方仍需根据目标模型为系统提示、当前输入和模型输出预留窗口。
        """
        policy = policy or ContextPolicy()
        gathering = self._gather_detailed(items)
        gathered = gathering.items
        isolated, isolation_drops = self._isolate_detailed(gathered, request, policy)
        selection = self._select_detailed(isolated, request, policy)
        compressed, compression_drops, compression_applied = self._compress_detailed(
            selection.items,
            request.token_budget,
            policy.compression_marker,
        )
        sections = self.structure(compressed, policy)
        messages = self._build_messages(
            system_instructions=system_instructions,
            sections=sections,
            current_input=request.current_input,
        )

        dropped_items = (
            gathering.dropped_items
            + isolation_drops
            + selection.dropped_items
            + compression_drops
        )
        token_used = sum(item.token_count for item in compressed)
        trace = self.trace(
            request=request,
            candidate_count=gathering.candidate_count,
            isolated_count=len(isolated),
            selected_items=compressed,
            dropped_items=dropped_items,
            token_used=token_used,
            compression_applied=compression_applied,
        )
        source_refs = list(
            dict.fromkeys(item.source_ref for item in compressed if item.source_ref)
        )
        compiled_token_count = self._count_tokens(
            "\n".join(message["content"] for message in messages)
        )
        return CompiledContext(
            request=request,
            messages=messages,
            sections=sections,
            selected_items=compressed,
            source_refs=source_refs,
            token_usage={
                "context_items": token_used,
                "context_items_budget": request.token_budget,
                "structured_context": self._count_tokens(
                    "\n\n".join(section.content for section in sections)
                ),
                "system_instructions": self._count_tokens(
                    system_instructions.strip()
                    if system_instructions and system_instructions.strip()
                    else ""
                ),
                "current_input": self._count_tokens(request.current_input),
                "compiled_messages": compiled_token_count,
            },
            dropped_items=dropped_items,
            compression_applied=compression_applied,
            trace=trace,
        )

    def trace(
        self,
        *,
        request: ContextRequest,
        candidate_count: int,
        isolated_count: int,
        selected_items: Iterable[ContextItem],
        dropped_items: list[dict[str, Any]],
        token_used: int,
        compression_applied: bool,
    ) -> ContextTrace:
        """生成一次编译的可解释追踪记录。"""
        selected_items = list(selected_items)
        return ContextTrace(
            request_id=request.request_id,
            candidate_count=candidate_count,
            isolated_count=isolated_count,
            selected_count=len(selected_items),
            token_budget=request.token_budget,
            token_used=token_used,
            compression_applied=compression_applied,
            selected_item_ids=[item.item_id for item in selected_items],
            dropped_items=list(dropped_items),
        )

    def _normalize_item(self, item: ContextItem | Mapping[str, Any]) -> ContextItem:
        if isinstance(item, ContextItem):
            return item
        return ContextItem(**dict(item))

    def _count_tokens(self, content: str) -> int:
        return max(0, int(self._token_counter(content)))

    def _isolate_detailed(
        self,
        items: list[ContextItem],
        request: ContextRequest,
        policy: ContextPolicy,
    ) -> tuple[list[ContextItem], list[dict[str, Any]]]:
        request_scope = request.effective_scope()
        isolated: list[ContextItem] = []
        dropped: list[dict[str, Any]] = []
        for item in items:
            reason: str | None = None
            if (
                policy.allowed_source_types is not None
                and item.source_type not in policy.allowed_source_types
            ):
                reason = "source_type_not_allowed"
            elif any(
                request_scope.get(key) != value for key, value in item.scope.items()
            ):
                reason = "scope_mismatch"
            if reason:
                dropped.append(
                    {
                        "item_id": item.item_id,
                        "source_ref": item.source_ref,
                        "reason": reason,
                    }
                )
                continue
            isolated.append(item)
        return isolated, dropped

    def _select_detailed(
        self,
        items: list[ContextItem],
        request: ContextRequest,
        policy: ContextPolicy,
    ) -> _SelectionResult:
        weights = (
            policy.relevance_weight,
            policy.recency_weight,
            policy.importance_weight,
        )
        weight_total = sum(weights)
        query_terms = _terms(request.current_input)
        scored: list[tuple[float, ContextItem]] = []
        protected: list[ContextItem] = []
        dropped: list[dict[str, Any]] = []
        for item in items:
            if self._is_protected(item, policy):
                protected.append(item)
                continue
            relevance = (
                item.relevance_score
                if item.relevance_score is not None
                else _relevance(item.content, query_terms)
            )
            if relevance < policy.min_relevance:
                dropped.append(
                    {
                        "item_id": item.item_id,
                        "source_ref": item.source_ref,
                        "reason": "below_min_relevance",
                        "relevance_score": relevance,
                    }
                )
                continue
            score = (
                policy.relevance_weight * relevance
                + policy.recency_weight
                * self._recency(item, policy.recency_half_life_seconds)
                + policy.importance_weight * item.importance
            ) / weight_total
            scored.append((score, item))

        scored.sort(
            key=lambda pair: (pair[0], _timestamp_value(pair[1].created_at)),
            reverse=True,
        )
        ordered = protected + [item for _, item in scored]
        protected_ids = {item.item_id for item in protected}
        selected: list[ContextItem] = []
        used_tokens = 0
        for item in ordered:
            is_protected = item.item_id in protected_ids
            if (
                policy.max_items is not None
                and len(selected) >= policy.max_items
                and not is_protected
            ):
                dropped.append(
                    {
                        "item_id": item.item_id,
                        "source_ref": item.source_ref,
                        "reason": "max_items",
                    }
                )
                continue
            if is_protected:
                selected.append(item)
                used_tokens += item.token_count
                continue
            if used_tokens + item.token_count <= request.token_budget:
                selected.append(item)
                used_tokens += item.token_count
            else:
                dropped.append(
                    {
                        "item_id": item.item_id,
                        "source_ref": item.source_ref,
                        "reason": "token_budget",
                    }
                )
        return _SelectionResult(items=selected, dropped_items=dropped)

    def _compress_detailed(
        self,
        items: list[ContextItem],
        token_budget: int,
        compression_marker: str,
    ) -> tuple[list[ContextItem], list[dict[str, Any]], bool]:
        if sum(item.token_count for item in items) <= token_budget:
            return list(items), [], False

        allocations = _allocate_token_budget(
            [item.token_count for item in items], token_budget
        )
        compressed: list[ContextItem] = []
        dropped: list[dict[str, Any]] = []
        for item, allocation in zip(items, allocations, strict=True):
            if allocation <= 0:
                dropped.append(
                    {
                        "item_id": item.item_id,
                        "source_ref": item.source_ref,
                        "reason": "compression_budget_exhausted",
                    }
                )
                continue
            if item.token_count <= allocation:
                compressed.append(item)
                continue
            truncated = self._truncate_item(item, allocation, compression_marker)
            if truncated is None:
                dropped.append(
                    {
                        "item_id": item.item_id,
                        "source_ref": item.source_ref,
                        "reason": "compression_item_removed",
                    }
                )
                continue
            compressed.append(truncated)
        return compressed, dropped, True

    def _truncate_item(
        self, item: ContextItem, max_tokens: int, marker: str
    ) -> ContextItem | None:
        if max_tokens <= 0:
            return None
        marker_tokens = self._count_tokens(marker)
        if marker_tokens >= max_tokens:
            content = _fit_prefix(item.content, max_tokens, self._count_tokens)
        else:
            prefix = _fit_marked_prefix(
                item.content,
                marker,
                max_tokens,
                self._count_tokens,
            )
            content = f"{prefix.rstrip()} {marker}".strip()
        if not content:
            return None
        return replace(
            item,
            content=content,
            token_count=self._count_tokens(content),
        )

    @staticmethod
    def _is_protected(item: ContextItem, policy: ContextPolicy) -> bool:
        return (
            item.always_include
            or item.source_type in policy.protected_source_types
            or item.item_id in policy.protected_item_ids
        )

    @staticmethod
    def _recency(item: ContextItem, half_life_seconds: float) -> float:
        created_at = item.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        age = max(0.0, (datetime.now(timezone.utc) - created_at).total_seconds())
        return math.pow(0.5, age / half_life_seconds)

    @staticmethod
    def _format_item(item: ContextItem) -> str:
        source = f" [source: {item.source_ref}]" if item.source_ref else ""
        return f"- {item.content}{source}"

    @staticmethod
    def _build_messages(
        *,
        system_instructions: str | None,
        sections: list[ContextSection],
        current_input: str,
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if system_instructions and system_instructions.strip():
            messages.append({"role": "system", "content": system_instructions.strip()})
        if sections:
            messages.append(
                {
                    "role": "system",
                    "content": "\n\n".join(section.content for section in sections),
                }
            )
        if current_input:
            messages.append({"role": "user", "content": current_input})
        return messages


@dataclass(slots=True)
class _GatherResult:
    items: list[ContextItem]
    dropped_items: list[dict[str, Any]]
    candidate_count: int


@dataclass(slots=True)
class _SelectionResult:
    items: list[ContextItem]
    dropped_items: list[dict[str, Any]]


def _terms(text: str) -> set[str]:
    return set(_TOKEN_PATTERN.findall(text.lower()))


def _relevance(content: str, query_terms: set[str]) -> float:
    if not query_terms:
        return 0.0
    content_terms = _terms(content)
    return min(1.0, len(content_terms & query_terms) / len(query_terms))


def _timestamp_value(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _fit_prefix(content: str, max_tokens: int, counter: TokenCounter) -> str:
    if max_tokens <= 0:
        return ""
    if counter(content) <= max_tokens:
        return content
    low, high = 0, len(content)
    while low < high:
        middle = (low + high + 1) // 2
        if counter(content[:middle]) <= max_tokens:
            low = middle
        else:
            high = middle - 1
    return content[:low]


def _fit_marked_prefix(
    content: str,
    marker: str,
    max_tokens: int,
    counter: TokenCounter,
) -> str:
    low, high = 0, len(content)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = f"{content[:middle].rstrip()} {marker}".strip()
        if counter(candidate) <= max_tokens:
            low = middle
        else:
            high = middle - 1
    return content[:low]


def _allocate_token_budget(demands: list[int], budget: int) -> list[int]:
    """在多条上下文之间按需公平分配预算，避免首项耗尽全部额度。"""
    allocations = [0] * len(demands)
    remaining_budget = max(0, budget)
    pending = list(range(len(demands)))
    while pending and remaining_budget > 0:
        share = remaining_budget // len(pending)
        if share == 0:
            for index in pending[:remaining_budget]:
                allocations[index] += 1
            break

        satisfied = [index for index in pending if demands[index] <= share]
        if satisfied:
            for index in satisfied:
                allocations[index] = demands[index]
                remaining_budget -= demands[index]
            pending = [index for index in pending if index not in satisfied]
            continue

        remainder = remaining_budget % len(pending)
        for position, index in enumerate(pending):
            allocations[index] = share + (1 if position < remainder else 0)
        break
    return allocations


def estimate_tokens(content: str) -> int:
    """无外部依赖的保守估算；生产接入时可注入模型对应 tokenizer。"""
    if not content:
        return 0
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", content))
    other_chars = len(re.sub(r"[\u4e00-\u9fff]", "", content))
    return max(1, chinese_chars + math.ceil(other_chars / 4))
