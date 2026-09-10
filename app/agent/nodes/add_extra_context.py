"""为 SQL 生成整理过滤后的额外上下文。

这个节点不进行召回，也不让 LLM 再次判断字段；只根据程序已经校验过的
filtered context，生成 SQL 节点容易消费的规则化说明。
"""

from typing import Any

from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.state import AgentState


def _build_table_context(table_infos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """整理表类型、业务粒度和用途，避免模型只看见物理表名。"""
    return [
        {
            "table_id": table["table_id"],
            "table_type": table["table_type"],
            "business_name": table["business_name"],
            "grain": table["grain"],
            "description": table["description"],
        }
        for table in table_infos
    ]


def _build_metric_context(
    metric_infos: list[dict[str, Any]],
    table_infos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """整理指标口径，并附上指标基础表的粒度。"""
    table_by_id = {table["table_id"]: table for table in table_infos}
    return [
        {
            "metric_id": metric["metric_id"],
            "metric_name": metric["metric_name"],
            "business_name": metric["business_name"],
            "base_table_id": metric["base_table_id"],
            "base_table_business_name": table_by_id.get(
                metric["base_table_id"], {}
            ).get("business_name", ""),
            "base_table_grain": table_by_id.get(
                metric["base_table_id"], {}
            ).get("grain", ""),
            "expression_sql": metric["expression_sql"],
            "aggregation_type": metric["aggregation_type"],
            "calculation_grain": metric.get("calculation_grain", ""),
            "aggregation_rule": metric.get("aggregation_rule", ""),
            "description": metric["description"],
            "unit": metric.get("unit"),
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
    table_infos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """整理关系两端的粒度及普通 JOIN、filter_exists 的使用规则。"""
    table_by_id = {table["table_id"]: table for table in table_infos}
    result = []
    for relationship in relationships:
        item = {**relationship}
        from_table = table_by_id.get(relationship["from_table_id"], {})
        to_table = table_by_id.get(relationship["to_table_id"], {})
        item["from_table_grain"] = from_table.get("grain", "")
        item["to_table_grain"] = to_table.get("grain", "")
        if relationship["relationship_type"] == "filter_exists":
            item["sql_usage"] = (
                "只能使用 EXISTS 筛选基础表记录；不能通过该关系把另一侧字段直接放入 SELECT 或 GROUP BY，"
                "也不能直接 JOIN 后聚合，否则一对多关系可能造成重复统计。"
            )
        else:
            item["sql_usage"] = (
                "可按 from_column_name = to_column_name 建立普通 JOIN；"
                "聚合前必须结合两端 grain 判断是否会改变指标统计粒度。"
            )
        result.append(item)
    return result


def _build_metric_dimension_context(
    metric_infos: list[dict[str, Any]],
    dimension_infos: list[dict[str, Any]],
    registered_infos: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """生成完整的指标与维度矩阵，明确区分支持和未登记组合。"""
    registered_by_pair = {
        (info["metric_id"], info["dimension_id"]): info
        for info in registered_infos
    }
    result = []
    for metric in metric_infos:
        for dimension in dimension_infos:
            registered = registered_by_pair.get(
                (metric["metric_id"], dimension["dimension_id"])
            )
            support_level = (
                registered.get("support_level", "supported")
                if registered
                else "unsupported"
            )
            result.append(
                {
                    "metric_id": metric["metric_id"],
                    "metric_business_name": metric["business_name"],
                    "dimension_id": dimension["dimension_id"],
                    "dimension_business_name": dimension["business_name"],
                    "dimension_table_id": dimension["table_id"],
                    "dimension_column_name": dimension["column_name"],
                    "supported": support_level == "supported",
                    "support_level": support_level,
                    "compatibility_note": (
                        registered["compatibility_note"]
                        if registered
                        else "未登记为支持组合，不能假设该指标可以安全按此维度拆分。"
                    ),
                    "usage_note": (
                        registered.get("usage_note", "")
                        if registered
                        else "该组合没有已登记的安全查询方式。"
                    ),
                }
            )
    return result


def _build_column_notes(table_infos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """整理字段格式等生成 SQL 时容易出错的说明。"""
    notes = []
    for table in table_infos:
        for column in table["columns"]:
            notes.append(
                {
                    "table_id": table["table_id"],
                    "column_id": column["column_id"],
                    "column_name": column["column_name"],
                    "business_name": column["business_name"],
                    "semantic_role": column["semantic_role"],
                    "data_type": column["data_type"],
                    "is_aggregatable": column["is_aggregatable"],
                    "description": column.get("description", ""),
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
    writer({"type": "progress", "step": step, "node": "add_extra_context", "status": "running"})

    table_infos = state.get("table_infos", [])
    relationships = state.get("relationship_infos", [])
    metric_infos = state.get("metric_infos", [])
    dimension_infos = state.get("dimension_infos", [])
    metric_dimension_infos = state.get("metric_dimension_infos", [])
    extra_context = {
        "table_context": _build_table_context(table_infos),
        "metric_context": _build_metric_context(metric_infos, table_infos),
        "filter_context": _build_filter_context(table_infos),
        "relationship_context": _build_relationship_context(
            relationships, table_infos
        ),
        "metric_dimension_context": _build_metric_dimension_context(
            metric_infos, dimension_infos, metric_dimension_infos
        ),
        "column_notes": _build_column_notes(table_infos),
        "sql_rules": [
            "只能使用 filtered context 中出现的表和字段。",
            "必须先根据 table_context.grain 和 metric_context.base_table_grain 判断每张表及指标的统计粒度。",
            "指标必须严格使用 metric_context.expression_sql。",
            "精确维度值使用 raw_value 作为数据库筛选值。",
            "relationship_type=filter_exists 时必须使用 EXISTS，不能直接 JOIN 后聚合。",
            "filter_exists 关系只能用于筛选，不能把被筛选侧字段直接用于 SELECT 或 GROUP BY。",
            "metric_dimension_context.support_level=unsupported 表示该组合不支持；support_level=conditional 时必须遵守 usage_note，不得把未登记组合当作已验证的安全组合。",
            "年份和月份筛选必须使用 dim_date.year_month_value；date_key 是整数代理键，禁止使用 LIKE。",
            "用户未明确指定日期口径时，只能使用 relationship_context 中已经保留的默认日期关系。",
            "所有聚合表达式必须使用 AS 别名，优先使用 metric_context.metric_id 作为稳定别名。",
            "result_columns.source_column_id 必须来自 column_notes.column_id；result_columns.source_metric_id 必须来自 metric_context.metric_id。",
        ],
    }
    writer(
        {
            "type": "extra_context",
            "step": step,
            "node": "add_extra_context",
            "status": "success",
            "extra_context": extra_context,
        }
    )
    return {"extra_context": extra_context}
