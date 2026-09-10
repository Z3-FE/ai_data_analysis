"""校验过滤选择，并补齐 SQL 生成所需的结构依赖。"""

import logging
import re
from collections import deque
from typing import Any

from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.state import AgentState

logger = logging.getLogger(__name__)
_IDENTIFIER_PATTERN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
_SQL_WORDS = {
    "SUM",
    "AVG",
    "COUNT",
    "DISTINCT",
    "MIN",
    "MAX",
    "CASE",
    "WHEN",
    "THEN",
    "ELSE",
    "END",
    "NULL",
    "AND",
    "OR",
}

# 用户没有明确指定日期口径时，各事实表使用固定的业务默认日期。
_DEFAULT_DATE_COLUMN_BY_TABLE = {
    "dw.fact_order": "purchase_date_key",
    "dw.fact_order_item": "purchase_date_key",
    "dw.fact_payment": "purchase_date_key",
    "dw.fact_review": "review_creation_date_key",
}

# 只有问句明确出现这些日期含义时，才切换到非默认日期关系。
_DATE_COLUMN_KEYWORDS = (
    ("review_answer_date_key", ("评价回复", "回复日期", "答复日期")),
    ("review_creation_date_key", ("评价创建", "评价日期", "评论日期")),
    ("estimated_delivery_date_key", ("预计送达", "预计收货")),
    ("delivered_customer_date_key", ("实际收货", "客户收货", "实际送达")),
    ("delivered_carrier_date_key", ("交付承运商", "承运商接收")),
    ("approved_date_key", ("审批日期", "批准日期", "审核日期")),
    ("shipping_limit_date_key", ("最晚发货", "发货期限")),
    ("purchase_date_key", ("下单日期", "购买日期", "下单时间")),
)


def _relationship_tables(relationship: dict[str, Any]) -> tuple[str, str]:
    return relationship["from_table_id"], relationship["to_table_id"]


def _find_table_path(
    start_table_id: str,
    target_table_id: str,
    relationships: list[dict[str, Any]],
    selected_column_ids: set[str],
    query: str,
) -> list[dict[str, Any]]:
    """在关系图中寻找基础表到目标表的一条最短路径。"""
    if start_table_id == target_table_id:
        return []
    graph: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for relationship in relationships:
        source, target = _relationship_tables(relationship)
        graph.setdefault(source, []).append((target, relationship))
        graph.setdefault(target, []).append((source, relationship))

    explicit_date_column = next(
        (
            column_name
            for column_name, keywords in _DATE_COLUMN_KEYWORDS
            if any(keyword in query for keyword in keywords)
        ),
        None,
    )

    def relationship_priority(item: tuple[str, dict[str, Any]]) -> tuple[int, str]:
        """优先问句明确日期，其次业务默认日期，避免共享 date_key 干扰。"""
        _, relationship = item
        source, target = _relationship_tables(relationship)
        from_column_name = relationship["from_column_name"]
        source_column_id = f"{source}.{from_column_name}"
        is_date_relationship = target == "dw.dim_date"

        if is_date_relationship and explicit_date_column:
            priority = 0 if from_column_name == explicit_date_column else 3
        elif is_date_relationship:
            default_date_column = _DEFAULT_DATE_COLUMN_BY_TABLE.get(source)
            if from_column_name == default_date_column:
                priority = 0
            elif source_column_id in selected_column_ids:
                priority = 1
            else:
                priority = 2
        elif source_column_id in selected_column_ids:
            priority = 0
        else:
            priority = 2
        return priority, relationship["relationship_id"]

    for edges in graph.values():
        edges.sort(key=relationship_priority)

    queue: deque[str] = deque([start_table_id])
    previous: dict[str, tuple[str, dict[str, Any]]] = {}
    visited = {start_table_id}
    while queue:
        current = queue.popleft()
        for neighbor, relationship in graph.get(current, []):
            if neighbor in visited:
                continue
            visited.add(neighbor)
            previous[neighbor] = (current, relationship)
            if neighbor == target_table_id:
                queue.clear()
                break
            queue.append(neighbor)

    if target_table_id not in previous:
        raise ValueError(
            f"找不到从指标基础表 {start_table_id} 到目标表 {target_table_id} 的关系路径。"
        )

    path: list[dict[str, Any]] = []
    current = target_table_id
    while current != start_table_id:
        parent, relationship = previous[current]
        path.append(relationship)
        current = parent
    path.reverse()
    return path


def _metric_dependency_columns(
    metric: dict[str, Any],
    columns_by_table: dict[str, dict[str, dict[str, Any]]],
) -> set[str]:
    """从指标表达式中找到基础表上的真实字段名。"""
    table_id = metric["base_table_id"]
    table_columns = columns_by_table.get(table_id, {})
    identifiers = {
        identifier
        for identifier in _IDENTIFIER_PATTERN.findall(metric["expression_sql"])
        if identifier.upper() not in _SQL_WORDS
    }
    return {
        column["column_id"]
        for column in table_columns.values()
        if column["column_name"] in identifiers
    }


def _exact_value_columns(table_infos: list[dict[str, Any]]) -> set[str]:
    return {
        column["column_id"]
        for table in table_infos
        for column in table["columns"]
        if any(value["exact_match"] for value in column.get("matched_values", []))
    }


def _resolve_table_id(table_id: str, tables_by_id: dict[str, dict[str, Any]]) -> str:
    """将唯一的短表名解析为候选上下文中的正式表 ID。

    LLM 偶尔会省略 schema。只有候选表中存在唯一同名后缀时才自动补齐，
    避免在多个 schema 存在同名表时凭空选择错误的表。
    """
    normalized_table_id = table_id.strip().strip("'")
    if normalized_table_id in tables_by_id:
        return normalized_table_id

    matches = [
        candidate_id
        for candidate_id in tables_by_id
        if candidate_id.rsplit(".", 1)[-1] == normalized_table_id
    ]
    if len(matches) == 1:
        logger.warning(
            "模型返回短表名，已按唯一候选补齐正式表 ID：%s -> %s",
            table_id,
            matches[0],
        )
        return matches[0]
    if len(matches) > 1:
        raise ValueError(
            f"模型选择的表名不唯一：{table_id}，候选表为 {sorted(matches)}"
        )
    return normalized_table_id


def _copy_filtered_tables(
    table_infos: list[dict[str, Any]],
    selected_columns_by_table: dict[str, set[str]],
) -> list[dict[str, Any]]:
    filtered_tables = []
    for table in table_infos:
        selected_column_ids = selected_columns_by_table.get(table["table_id"], set())
        if not selected_column_ids:
            continue
        filtered_table = {**table}
        filtered_columns = []
        for column in table["columns"]:
            if column["column_id"] not in selected_column_ids:
                continue
            # 只有精确命中的维度值才可以作为本次 SQL 的确定条件。
            # 语义召回的其他值仍然可以用于解释，但不能误导 SQL 生成。
            filtered_columns.append(
                {
                    **column,
                    "matched_values": [
                        value
                        for value in column.get("matched_values", [])
                        if value.get("exact_match", False)
                    ],
                }
            )
        filtered_table["columns"] = filtered_columns
        filtered_tables.append(filtered_table)
    return filtered_tables


async def reconcile_filtered_context(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> dict[str, Any]:
    """将 LLM 选择清单校验、裁剪，并补齐指标和关系依赖。"""
    writer = runtime.stream_writer
    step = "补全过滤后的上下文"
    writer({"type": "progress", "step": step, "node": "reconcile_filtered_context", "status": "running"})

    table_infos = state.get("table_infos", [])
    metric_infos = state.get("metric_infos", [])
    relationships = state.get("relationship_infos", [])
    metric_selection = set(state.get("metric_selection", []))
    table_selection = state.get("table_selection", {})

    tables_by_id = {table["table_id"]: table for table in table_infos}
    columns_by_table = {
        table_id: {column["column_id"]: column for column in table["columns"]}
        for table_id, table in tables_by_id.items()
    }
    metrics_by_id = {metric["metric_id"]: metric for metric in metric_infos}

    unknown_metrics = metric_selection.difference(metrics_by_id)
    if unknown_metrics:
        raise ValueError(f"模型选择了不存在的指标：{sorted(unknown_metrics)}")

    selected_columns_by_table: dict[str, set[str]] = {}
    for raw_table_id, column_ids in table_selection.items():
        table_id = _resolve_table_id(str(raw_table_id), tables_by_id)
        if table_id not in tables_by_id:
            raise ValueError(f"模型选择了不存在的表：{table_id}")
        available_columns = columns_by_table[table_id]
        unknown_columns = set(column_ids).difference(available_columns)
        if unknown_columns:
            raise ValueError(
                f"模型选择了不存在的字段：{sorted(unknown_columns)}"
            )
        selected_columns_by_table[table_id] = set(column_ids)

    selected_metrics = [
        metric for metric in metric_infos if metric["metric_id"] in metric_selection
    ]
    required_table_ids = set(selected_columns_by_table)
    required_table_ids.update(metric["base_table_id"] for metric in selected_metrics)

    exact_value_column_ids = _exact_value_columns(table_infos)
    exact_value_tables = {
        table["table_id"]
        for table in table_infos
        for column in table["columns"]
        if column["column_id"] in exact_value_column_ids
    }
    required_table_ids.update(exact_value_tables)

    for metric in selected_metrics:
        table_id = metric["base_table_id"]
        if table_id not in columns_by_table:
            raise ValueError(f"指标基础表不在候选表中：{table_id}")
        selected_columns_by_table.setdefault(table_id, set()).update(
            _metric_dependency_columns(metric, columns_by_table)
        )

    # 精确维度值对应的字段必须保留，即使 LLM 没有在 table_selection 中选中它。
    for table in table_infos:
        for column in table["columns"]:
            if column["column_id"] in exact_value_column_ids:
                selected_columns_by_table.setdefault(table["table_id"], set()).add(
                    column["column_id"]
                )

    related_paths: list[dict[str, Any]] = []
    for target_table_id in sorted(required_table_ids):
        if not selected_metrics:
            break
        for metric in selected_metrics:
            if metric["base_table_id"] == target_table_id:
                continue
            path = _find_table_path(
                metric["base_table_id"],
                target_table_id,
                relationships,
                {
                    column_id
                    for column_ids in selected_columns_by_table.values()
                    for column_id in column_ids
                },
                state.get("input_text", ""),
            )
            related_paths.extend(path)
            break

    related_paths_by_id = {
        relationship["relationship_id"]: relationship
        for relationship in related_paths
    }
    for relationship in related_paths_by_id.values():
        source, target = _relationship_tables(relationship)
        required_table_ids.update((source, target))
        selected_columns_by_table.setdefault(source, set()).update(
            column_id
            for column_id in columns_by_table[source]
            if column_id == f"{source}.{relationship['from_column_name']}"
        )
        selected_columns_by_table.setdefault(target, set()).update(
            column_id
            for column_id in columns_by_table[target]
            if column_id == f"{target}.{relationship['to_column_name']}"
        )

    final_tables = _copy_filtered_tables(table_infos, selected_columns_by_table)
    final_table_ids = {table["table_id"] for table in final_tables}
    final_relationships = [
        relationship
        for relationship in relationships
        if relationship["relationship_id"] in related_paths_by_id
        and relationship["from_table_id"] in final_table_ids
        and relationship["to_table_id"] in final_table_ids
    ]

    final_metric_infos = [
        metric for metric in metric_infos if metric["metric_id"] in metric_selection
    ]
    final_dimension_infos = [
        dimension
        for dimension in state.get("dimension_infos", [])
        if dimension["table_id"] in final_table_ids
    ]
    final_dimension_ids = {dimension["dimension_id"] for dimension in final_dimension_infos}
    final_metric_ids = {metric["metric_id"] for metric in final_metric_infos}
    final_metric_dimension_infos = [
        info
        for info in state.get("metric_dimension_infos", [])
        if info["metric_id"] in final_metric_ids
        and info["dimension_id"] in final_dimension_ids
    ]
    writer(
        {
            "type": "filtered_context",
            "step": step,
            "node": "reconcile_filtered_context",
            "status": "success",
            "table_ids": sorted(final_table_ids),
            "metric_ids": [metric["metric_id"] for metric in final_metric_infos],
            "relationship_ids": [
                relationship["relationship_id"] for relationship in final_relationships
            ],
            # 输出过滤后的完整上下文，便于确认指标字段和 JOIN 字段是否补齐。
            "table_infos": final_tables,
            "metric_infos": final_metric_infos,
            "dimension_infos": final_dimension_infos,
            "relationship_infos": final_relationships,
            "metric_dimension_infos": final_metric_dimension_infos,
        }
    )
    return {
        "table_infos": final_tables,
        "metric_infos": final_metric_infos,
        "dimension_infos": final_dimension_infos,
        "relationship_infos": final_relationships,
        "metric_dimension_infos": final_metric_dimension_infos,
    }
