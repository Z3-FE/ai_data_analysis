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
步骤1-7 ：
四路召回结果
│
├── table_candidates
│      └── 直接创建 tables_map
│
├── column_candidates
│      └── 创建 columns_map
│             └── column.table_id ──────────────┐
│                                               │
├── metric_candidates                           │
│      └── 创建 metrics_map                     │
│             └── metric.base_table_id ─────────┤
│                                               │
└── dimension_value_candidates                  │
       └── candidate.column_id                  │
              ├── columns_map 已存在：直接使用   │
              └── columns_map 不存在：           │
                    从 Meta MySQL 补齐字段        │
              └── 将 matched_value 挂到字段      │
                     └── column.table_id ────────┘
                                                │
                                                ▼
                            full_table_sources_by_id
                            table_id → matched_sources
                                                │
                         ┌───────────────────────┴──────────────────────┐
                         │                                              │
                  tables_map 已存在                              tables_map 不存在
                         │                                              │
                  合并 matched_sources                     从 Meta MySQL 补齐表实体
                         │                                              │
                         └───────────────────────┬──────────────────────┘
                                                 ▼
                                            tables_map
                                                 │
                      从 Meta MySQL 查询候选表下全部可查询字段
                                                 │
                     ┌───────────────────────────┴────────────────────┐
                     │                                                │
               已存在于 columns_map                         不存在于 columns_map
                     │                                                │
              复用已召回字段及其值                     创建 metadata_completion 字段
                     │                                                │
                     └───────────────────────────┬────────────────────┘
                                                 ▼
                         将所有 columns_map 字段挂到所属 tables_map
                                                 │
                                                 ▼
                                      table → columns → matched_values


合并结果
├── table_infos
│   └── table
│       └── columns
│           └── matched_values
├── metric_infos
├── relationship_infos
├── metric_dimension_infos
└── dimension_infos
可直接消费的表、字段、指标、JOIN 关系和指标维度兼容上下文。


metric_infos：算什么
dimension_infos：按什么分析
metric_dimension_infos：这个指标是否支持这个分析维度
table_infos：数据来自哪些表和字段
relationship_infos：表之间如何 JOIN

"""

import logging
from dataclasses import asdict, fields
from typing import Any, TypeVar

from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.state import AgentState
from app.entities.agent.agent_merge_context import (
    MatchedSource,
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


def _build_entity(entity_type: type[EntityType], payload: dict[str, Any]) -> EntityType:
    """只取实体声明的字段，把召回 payload 还原为业务实体。"""
    # 只保留目标实体声明的业务字段，忽略向量检索产生的辅助字段。
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
        aliases=list(candidate["aliases"]),
        description=candidate["description"],
        exact_match=bool(candidate["exact_match"]),
        exact_priority=int(candidate["exact_priority"]),
        match_types=dict(candidate["match_types"]),
        matched_terms=list(candidate["matched_terms"]),
        rrf_score=float(candidate["rrf_score"]),
        es_score=candidate["es_score"],
        vector_score=candidate["vector_score"],
    )


def _matched_sources_to_state(sources: set[MatchedSource]) -> dict[str, str]:
    """把内部召回来源集合转换为便于查看的“来源标识 -> 中文含义”映射。"""
    return {
        source.value: source.description
        for source in sorted(sources, key=lambda item: item.value)
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
        table_entity: MetaTables = _build_entity(MetaTables, candidate["payload"])
        tables_map[table_entity.table_id] = MergedTableInfo(
            table=table_entity, matched_sources={MatchedSource.TABLE_RECALL}
        )

    columns_map: dict[str, MergedColumnInfo] = {}
    for candidate in state.get("column_candidates", []):
        column = _build_entity(MetaColumns, candidate["payload"])
        columns_map[column.column_id] = MergedColumnInfo(
            column=column, matched_sources={MatchedSource.COLUMN_RECALL}
        )

    metrics_map: dict[str, MergedMetricInfo] = {}
    for candidate in state.get("metrics_candidates", []):
        metric = _build_entity(MetaMetrics, candidate["payload"])
        metrics_map[metric.metric_id] = MergedMetricInfo(
            metric=metric, matched_sources={MatchedSource.METRIC_RECALL}
        )

    dimension_value_candidates = state.get("dimension_value_candidates", [])

    # 记录真正由字段/维度值召回带来的字段。
    # 后面会把候选表下全部 queryable 字段补进 columns_map，
    # 但查询 metric_dimensions 时不能把这些“目录补齐字段”误当成用户召回的维度。
    relevant_column_ids = set(columns_map)

    # 步骤 2：从 Meta MySQL 补齐维度值所属但字段召回未命中的字段。
    value_column_ids: set[str] = {
        candidate["column_id"] for candidate in dimension_value_candidates
    }
    # 只有维度值对应的字段尚未被字段召回命中时，才从 Meta MySQL 补齐。
    missing_column_ids = value_column_ids.difference(columns_map)
    missing_columns = await repository.get_columns_by_ids(sorted(missing_column_ids))
    loaded_column_ids = {column.column_id for column in missing_columns}
    # 维度值涉及的字段，但字段召回没有命中
    unresolved_column_ids = missing_column_ids.difference(loaded_column_ids)
    if unresolved_column_ids:
        raise ValueError(
            "维度值召回结果引用了不存在或不可查询的 Meta 字段："
            f"{sorted(unresolved_column_ids)}"
        )
    for column in missing_columns:
        columns_map[column.column_id] = MergedColumnInfo(
            column=column,
            matched_sources={MatchedSource.DIMENSION_VALUE_COMPLETION},
        )
        relevant_column_ids.add(column.column_id)

    # 步骤 3：把每条维度值及其检索证据挂到对应字段。
    for candidate in dimension_value_candidates:
        column_info = columns_map[candidate["column_id"]]
        column_info.matched_sources.add(MatchedSource.DIMENSION_VALUE_RECALL)
        column_info.matched_values.append(_build_matched_value(candidate))

    # 步骤 4：汇总表召回、字段所属表和指标基础表，形成完整的 table_id 来源映射。
    # 维度值已经在步骤 2、3 绑定到 columns_map，因此会随字段来源传递到所属表，
    # 不再重复遍历 dimension_value_candidates。
    full_table_sources_by_id: dict[str, set[MatchedSource]] = {
        table_id: set(table_info.matched_sources)
        for table_id, table_info in tables_map.items()
    }
    for column_info in columns_map.values():
        full_table_sources_by_id.setdefault(column_info.column.table_id, set()).update(
            column_info.matched_sources
        )
    for metric_info in metrics_map.values():
        full_table_sources_by_id.setdefault(
            metric_info.metric.base_table_id, set()
        ).add(
            MatchedSource.METRIC_RECALL
        )

    # 步骤 5：批量补齐没有被直接召回的表元数据。
    missing_table_ids = set(full_table_sources_by_id).difference(tables_map)
    missing_tables = await repository.get_tables_by_ids(sorted(missing_table_ids))
    loaded_table_ids = {table.table_id for table in missing_tables}
    unresolved_table_ids = missing_table_ids.difference(loaded_table_ids)
    if unresolved_table_ids:
        raise ValueError(
            "召回结果引用了不存在或未启用的 Meta 表："
            f"{sorted(unresolved_table_ids)}"
        )
    for table in missing_tables:
        tables_map[table.table_id] = MergedTableInfo(
            table=table,
            matched_sources=set(full_table_sources_by_id[table.table_id]),
        )
    for table_id, sources in full_table_sources_by_id.items():
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
                column=column,
                matched_sources={MatchedSource.METADATA_COMPLETION},
            )
            columns_map[column.column_id] = column_info
        tables_map[column.table_id].columns[column.column_id] = column_info

    # 步骤 7：确认所有真正相关的字段都已进入候选表字段目录。
    # 如果缺失，说明召回结果与 Meta MySQL 的可查询字段不一致，应尽早暴露问题。
    loaded_column_ids = {
        column_id
        for table_info in tables_map.values()
        for column_id in table_info.columns
    }
    unresolved_column_ids = relevant_column_ids.difference(loaded_column_ids)
    if unresolved_column_ids:
        raise ValueError(
            "字段召回结果未能挂载到候选表的可查询字段目录："
            f"{sorted(unresolved_column_ids)}"
        )

    # 步骤 8：查询候选表之间已登记的 JOIN 关系。
    relationships = await repository.get_relationships_by_table_ids(sorted(tables_map))

    # 步骤 9：查询当前候选指标与召回维度之间的兼容关系。
    # 这里只使用字段/维度值真正召回到的字段，避免目录补齐字段扩大维度范围。
    dimensions = await repository.get_dimensions_by_column_ids(
        sorted(relevant_column_ids)
    )
    dimension_ids = [dimension.dimension_id for dimension in dimensions]
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
    dimension_infos = [asdict(dimension) for dimension in dimensions]
    relationship_infos = [asdict(relationship) for relationship in relationships]
    metric_dimension_states = [asdict(info) for info in metric_dimension_infos]

    result = {
        # 候选表、字段以及字段下命中的真实维度值
        "table_infos": table_infos,
        # 用户可能要计算的指标
        "metric_infos": metric_infos,
        # 用户可能用于拆分和筛选指标的业务维度
        "dimension_infos": dimension_infos,
        # 候选表之间可以使用的 JOIN 关系
        "relationship_infos": relationship_infos,
        # 指标和维度之间的可分析关系
        "metric_dimension_infos": metric_dimension_states,
    }
    writer({"type": "progress", "step": step, "status": "success"})
    writer({"type": "retrieved_info", "step": step, **result})
    logger.info(
        "召回合并完成 tables=%s columns=%s metrics=%s dimensions=%s "
        "relationships=%s metric_dimensions=%s",
        len(table_infos),
        len(columns_map),
        len(metric_infos),
        len(dimension_infos),
        len(relationship_infos),
        len(metric_dimension_states),
    )
    return result
