"""为 SQL 生成整理过滤后的额外上下文。

这个节点不进行召回，也不让 LLM 再次判断字段；只根据程序已经校验过的
filtered context，生成 SQL 节点容易消费的规则化说明。
"""

from typing import Any

from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.state import AgentState


def _build_metric_context(metric_infos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """整理指标口径和 SQL 表达式。"""
    return [
        {
            "metric_id": metric["metric_id"],
            "business_name": metric["business_name"],
            "base_table_id": metric["base_table_id"],
            "expression_sql": metric["expression_sql"],
            "aggregation_type": metric["aggregation_type"],
            "description": metric["description"],
        }
        for metric in metric_infos
    ]


def _build_filter_context(table_infos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """提取带有精确维度值的筛选条件。"""
    filters = []
    for table in table_infos:
        for column in table["columns"]:
            for value in column.get("matched_values", []):
                if not value.get("exact_match", False):
                    continue
                filters.append(
                    {
                        "column_id": column["column_id"],
                        "table_id": table["table_id"],
                        "column_name": column["column_name"],
                        "raw_value": value["raw_value"],
                        "display_name": value["display_name"],
                    }
                )
    return filters


def _build_relationship_context(
    relationships: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """整理普通 JOIN 和 filter_exists 的使用规则。"""
    result = []
    for relationship in relationships:
        item = {**relationship}
        if relationship["relationship_type"] == "filter_exists":
            item["sql_usage"] = (
                "使用 EXISTS 筛选基础表记录，不要直接 JOIN 后聚合，避免一对多关系造成重复统计。"
            )
        else:
            item["sql_usage"] = "可按 from_column_name = to_column_name 建立普通 JOIN。"
        result.append(item)
    return result


def _build_column_notes(table_infos: list[dict[str, Any]]) -> list[dict[str, str]]:
    """整理字段格式等生成 SQL 时容易出错的说明。"""
    notes = []
    for table in table_infos:
        for column in table["columns"]:
            description = column.get("description", "")
            if description:
                notes.append(
                    {
                        "column_id": column["column_id"],
                        "column_name": column["column_name"],
                        "description": description,
                    }
                )
    return notes


async def add_extra_context(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> dict[str, Any]:
    """把过滤后的元数据转换成 SQL 生成专用的额外上下文。"""
    writer = runtime.stream_writer
    step = "补充 SQL 生成上下文"
    writer({"type": "progress", "step": step, "status": "running"})

    table_infos = state.get("table_infos", [])
    relationships = state.get("relationship_infos", [])
    metric_infos = state.get("metric_infos", [])
    extra_context = {
        "metric_context": _build_metric_context(metric_infos),
        "filter_context": _build_filter_context(table_infos),
        "relationship_context": _build_relationship_context(relationships),
        "column_notes": _build_column_notes(table_infos),
        "sql_rules": [
            "只能使用 filtered context 中出现的表和字段。",
            "指标必须严格使用 metric_context.expression_sql。",
            "精确维度值使用 raw_value 作为数据库筛选值。",
            "relationship_type=filter_exists 时必须使用 EXISTS，不能直接 JOIN 后聚合。",
            "年份和月份筛选必须使用 dim_date.year_month_value；date_key 是整数代理键，禁止使用 LIKE。",
            "用户未明确指定日期口径时，只能使用 relationship_context 中已经保留的默认日期关系。",
        ],
    }
    writer(
        {
            "type": "extra_context",
            "step": step,
            "status": "success",
            "extra_context": extra_context,
        }
    )
    return {"extra_context": extra_context}
