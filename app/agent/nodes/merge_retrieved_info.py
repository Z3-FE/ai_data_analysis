"""四路召回信息合并节点。

负责把 tables、columns、metrics 和 dimension_values 四路候选整理成后续过滤节点
table_infos
告诉 SQL 节点“数据在哪里、有哪些字段和值”

metric_infos
告诉 SQL 节点“要计算什么”

relationship_infos
告诉 SQL 节点“表之间怎么连接”

metric_dimension_infos
告诉 SQL 节点“指标能不能按某个维度分析”


整体流程：
tables 向量召回 ───────────────┐
columns → table_id ───────────┤
metrics → base_table_id ──────┼→ 合并候选 table_id
dimension values → table_id ──┘
                              ↓
                    从 Meta MySQL 补齐表元数据
                              ↓
                         table_infos


可直接消费的表、字段、指标、JOIN 关系和指标维度兼容上下文。

"""

import json
import logging
from dataclasses import asdict, fields
from typing import Any, TypeVar

from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.state import AgentState
from app.entities.agent.agent_merge_context import (
    MatchedDimensionValue,
    MergedColumnInfo,
    MergedMetricInfo,
    MergedTableInfo,
)
from app.entities.meta.meta_columns import MetaColumns
from app.entities.meta.meta_metrics import MetaMetrics
from app.entities.meta.meta_tables import MetaTables

logger = logging.getLogger(__name__)
EntityType = TypeVar("EntityType")

# 召回来源在内部只保存稳定的英文标识，输出到 AgentState/SSE 时再补充中文含义。
MATCHED_SOURCE_DESCRIPTIONS = {
    "table_recall": "表语义召回直接命中该表",
    "column_recall": "字段语义召回命中，字段所属表因此成为候选表",
    "metric_recall": "指标语义召回命中，指标基础表因此成为候选表",
    "dimension_value_recall": "维度值召回命中，维度值所属字段和表因此成为候选",
    "metadata_completion": "根据候选表从 Meta MySQL 补齐的可查询字段",
}


def _parse_aliases(raw_aliases: Any) -> list[str]:
    """把 MySQL/Qdrant 中可能存在的 JSON 字符串统一转换为字符串列表。"""
    if raw_aliases is None:
        return []
    if isinstance(raw_aliases, list):
        return [str(alias) for alias in raw_aliases]
    if isinstance(raw_aliases, str):
        parsed = json.loads(raw_aliases)
        if not isinstance(parsed, list):
            raise ValueError(f"aliases 必须是 JSON 数组，实际是：{raw_aliases!r}")
        return [str(alias) for alias in parsed]
    raise TypeError(f"aliases 必须是 list、JSON 字符串或 None，实际类型：{type(raw_aliases).__name__}")


def _build_entity(entity_type: type[EntityType], payload: dict[str, Any]) -> EntityType:
    """只取实体声明的字段，把召回 payload 还原为业务实体。"""
    # fields(entity_type) ->( Field(name="table_id", type=str), 。。。) 元组，然后.name进行获取key
    field_names: set[str] = {field.name for field in fields(entity_type)}
    entity_payload = {key: payload[key] for key in field_names}
    return entity_type(**entity_payload)



def _build_matched_value(candidate: dict[str, Any]) -> MatchedDimensionValue:
    """把维度值融合结果转换为字段下的真实值实体。"""
    return MatchedDimensionValue(
        value_id=candidate["value_id"],
        dimension_id=candidate["dimension_id"],
        raw_value=candidate["raw_value"],
        normalized_value=candidate["normalized_value"],
        display_name=candidate["display_name"],
        aliases=_parse_aliases(candidate["aliases"]),
        description=candidate["description"],
        exact_match=bool(candidate["exact_match"]),
        exact_priority=int(candidate["exact_priority"]),
        match_types=dict(candidate["match_types"]),
        matched_terms=list(candidate["matched_terms"]),
        rrf_score=float(candidate["rrf_score"]),
        es_score=candidate["es_score"],
        vector_score=candidate["vector_score"],
    )


def _matched_sources_to_state(sources: set[str]) -> dict[str, str]:
    """把内部召回来源集合转换为便于查看的“来源标识 -> 中文含义”映射。"""
    return {
        source: MATCHED_SOURCE_DESCRIPTIONS[source]
        for source in sorted(sources)
    }


def _column_to_state(column_info: MergedColumnInfo) -> dict[str, Any]:
    """把合并字段实体转换成 AgentState 字典。"""
    return {
        **asdict(column_info.column),
        "matched_sources": _matched_sources_to_state(column_info.matched_sources),
        "matched_values": [asdict(value) for value in column_info.matched_values],
    }


def _table_to_state(table_info: MergedTableInfo) -> dict[str, Any]:
    """把合并表实体及其字段转换成 AgentState 字典。"""
    return {
        **asdict(table_info.table),
        "matched_sources": _matched_sources_to_state(table_info.matched_sources),
        "columns": [
            _column_to_state(column_info)
            for column_info in sorted(
                table_info.columns.values(),
                key=lambda item: item.column.column_id,
            )
        ],
    }


def _metric_to_state(metric_info: MergedMetricInfo) -> dict[str, Any]:
    """把合并指标实体转换成 AgentState 字典。"""
    return {
        **asdict(metric_info.metric),
        "matched_sources": _matched_sources_to_state(metric_info.matched_sources),
    }


async def merge_retrieved_info(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> AgentState:
    """合并四路召回结果，并补齐结构化 Meta MySQL 上下文。"""
    writer = runtime.stream_writer
    step = "合并召回信息"
    writer({"type": "progress", "step": step, "status": "running"})
    repository = runtime.context["meta_catalog_repository"]

    # 步骤 1：把 State 中的召回字典还原为内部业务实体，并按业务主键建立 Map。
    tables_map: dict[str, MergedTableInfo] = {}
    for candidate in state.get("table_candidates", []):
        # 获取Table实体
        table_entity: MetaTables = _build_entity(MetaTables, candidate['payload'])
        # 整理Table_map {id: MergedTableInfo }
        tables_map[table_entity.table_id] = MergedTableInfo(
            table=table_entity, matched_sources={"table_recall"}
        )

    columns_map: dict[str, MergedColumnInfo] = {}
    for candidate in state.get("column_candidates", []):
        column = _build_entity(MetaColumns, candidate['payload'])
        columns_map[column.column_id] = MergedColumnInfo(
            column=column, matched_sources={"column_recall"}
        )

    metrics_map: dict[str, MergedMetricInfo] = {}
    for candidate in state.get("metrics_candidates", []):
        metric = _build_entity(MetaMetrics, candidate['payload'])
        metrics_map[metric.metric_id] = MergedMetricInfo(
            metric=metric, matched_sources={"metric_recall"}
        )

    dimension_value_candidates = state.get("dimension_value_candidates", [])

    # 记录真正由字段/维度值召回带来的字段。
    # 后面会把候选表下全部 queryable 字段补进 columns_map，
    # 但查询 metric_dimensions 时不能把这些“目录补齐字段”误当成用户召回的维度。
    recalled_column_ids = set(columns_map)

    # 步骤 2：维度值必须绑定到真实字段；先批量补齐没有被字段召回命中的字段。
    value_column_ids = {candidate["column_id"] for candidate in dimension_value_candidates}
    missing_column_ids = value_column_ids.difference(columns_map)
    missing_columns = await repository.get_columns_by_ids(sorted(missing_column_ids))
    loaded_column_ids = {column.column_id for column in missing_columns}
    unresolved_column_ids = missing_column_ids.difference(loaded_column_ids)
    if unresolved_column_ids:
        raise ValueError(
            "维度值召回结果引用了不存在或不可查询的 Meta 字段："
            f"{sorted(unresolved_column_ids)}"
        )
    for column in missing_columns:
        columns_map[column.column_id] = MergedColumnInfo(
            column=column, matched_sources={"dimension_value_recall"}
        )
        recalled_column_ids.add(column.column_id)

    # 步骤 3：把 raw_value、中文展示名和检索证据挂到所属字段。
    for candidate in dimension_value_candidates:
        column_info = columns_map[candidate["column_id"]]
        column_info.matched_sources.add("dimension_value_recall")
        column_info.matched_values.append(_build_matched_value(candidate))

    # 步骤 4：候选表来自表召回、字段所属表、指标基础表和维度值所属表。
    table_sources: dict[str, set[str]] = {
        table_id: set(table_info.matched_sources)
        for table_id, table_info in tables_map.items()
    }
    for column_info in columns_map.values():
        table_sources.setdefault(column_info.column.table_id, set()).update(
            column_info.matched_sources
        )
    for metric_info in metrics_map.values():
        table_sources.setdefault(metric_info.metric.base_table_id, set()).add(
            "metric_recall"
        )
    for candidate in dimension_value_candidates:
        table_sources.setdefault(candidate["table_id"], set()).add(
            "dimension_value_recall"
        )

    # 步骤 5：批量补齐没有被直接召回的表元数据。
    missing_table_ids = set(table_sources).difference(tables_map)
    missing_tables = await repository.get_tables_by_ids(sorted(missing_table_ids))
    for table in missing_tables:
        tables_map[table.table_id] = MergedTableInfo(
            table=table, matched_sources=set(table_sources[table.table_id])
        )
    for table_id, sources in table_sources.items():
        tables_map[table_id].matched_sources.update(sources)

    # 步骤 6：批量补齐候选表下全部启用且可查询字段，供后续表字段过滤使用。
    # 这些字段属于候选表目录，不代表它们都被当前问题召回。
    catalog_columns = await repository.get_queryable_columns_by_table_ids(
        sorted(tables_map)
    )
    for column in catalog_columns:
        column_info = columns_map.get(column.column_id)
        if column_info is None:
            column_info = MergedColumnInfo(
                column=column, matched_sources={"metadata_completion"}
            )
            columns_map[column.column_id] = column_info
        tables_map[column.table_id].columns[column.column_id] = column_info

    # 步骤 7：补上已召回但不在表字段批量结果中的字段，保持合并结果完整。
    for column_id, column_info in columns_map.items():
        tables_map[column_info.column.table_id].columns[column_id] = column_info

    # 步骤 8：查询候选表之间已登记的 JOIN 关系。
    relationships = await repository.get_relationships_by_table_ids(sorted(tables_map))

    # 步骤 9：查询当前候选指标与召回维度之间的兼容关系。
    # 这里只使用字段/维度值真正召回到的字段，避免目录补齐字段扩大维度范围。
    recalled_dimension_ids = await repository.get_dimension_ids_by_column_ids(
        sorted(recalled_column_ids)
    )
    dimension_ids = sorted(
        set(recalled_dimension_ids)
        | {candidate["dimension_id"] for candidate in dimension_value_candidates}
    )
    metric_dimension_infos = await repository.get_metric_dimension_infos(
        metric_ids=sorted(metrics_map),
        dimension_ids=dimension_ids,
    )

    # 步骤 10：内部计算结束，只在写入 AgentState/SSE 前转换为普通字典。
    table_infos = [
        _table_to_state(table_info)
        for table_info in sorted(
            tables_map.values(), key=lambda item: item.table.table_id
        )
    ]
    metric_infos = [
        _metric_to_state(metric_info)
        for metric_info in sorted(
            metrics_map.values(), key=lambda item: item.metric.metric_id
        )
    ]
    relationship_infos = [asdict(relationship) for relationship in relationships]
    metric_dimension_states = [asdict(info) for info in metric_dimension_infos]

    result = {
        # 候选表、字段以及字段下命中的真实维度值
        "table_infos": table_infos,
        # 用户可能要计算的指标
        "metric_infos": metric_infos,
        # 候选表之间可以使用的 JOIN 关系
        "relationship_infos": relationship_infos,
        # 指标和维度之间的可分析关系
        "metric_dimension_infos": metric_dimension_states,
    }
    writer({"type": "progress", "step": step, "status": "success"})
    writer({"type": "retrieved_info", "step": step, **result})
    logger.info(
        "召回合并完成 tables=%s columns=%s metrics=%s relationships=%s",
        len(table_infos),
        len(columns_map),
        len(metric_infos),
        len(relationship_infos),
    )
    return result
