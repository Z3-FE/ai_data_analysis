"""在 SQL 执行后补齐查询结果的字段语义和展示映射。

本节点位于 Query Agent 的 SQL 执行之后，负责把数据库真实结果和 SQL
生成阶段的字段声明合并为后续分析、图表和报告可以消费的结构化结果。

整体流程：

步骤 1：读取 SQL 执行结果和 SQL 生成阶段的 result_columns 声明。
步骤 2：按真实返回字段逐列确认字段、指标和业务维度来源。
步骤 3：收集维度字段的真实值，批量准备 Meta 维度值查询请求。
步骤 4：从 Meta 查询维度值的正式展示名称。
步骤 5：保留 SQL 原始结果，同时生成面向用户的展示结果副本。
步骤 6：汇总映射来源和缺失映射限制，返回结果增强数据。

原始 sql_result 始终保留数据库值；display_sql_result 只用于展示，不能
覆盖原始值，也不能替代依赖任务继续使用的原始查询结果。
"""

from collections import defaultdict
from typing import Any

from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.result_schema import ResultColumn
from app.agent.state import AgentState


def _result_keys(rows: list[dict[str, Any]]) -> list[str]:
    """按首次出现顺序收集 SQL 结果字段。"""
    return list(dict.fromkeys(key for row in rows for key in row))


def _column_candidates(table_infos: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按字段名、字段 ID 建立字段候选。"""
    candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for table in table_infos:
        for column in table.get("columns", []):
            column_info = {**column, "table_id": table["table_id"]}
            for key in (column.get("column_name", ""), column.get("column_id", "")):
                if key:
                    candidates[key].append(column_info)
    return candidates


def _metric_candidates(metric_infos: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按指标 ID 和物理名称建立指标候选。"""
    candidates = {}
    for metric in metric_infos:
        for key in (metric.get("metric_id", ""), metric.get("metric_name", "")):
            if key:
                candidates[key] = metric
    return candidates


def _dimension_by_column(dimension_infos: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """建立字段 ID 到业务维度的映射。"""
    dimensions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for dimension in dimension_infos:
        column_id = f"{dimension['table_id']}.{dimension['column_name']}"
        dimensions[column_id].append(dimension)
    return dimensions


def _declared_columns(state: AgentState) -> dict[str, dict[str, Any]]:
    """读取 SQL 生成阶段声明的结果字段契约。"""
    return {
        item["result_name"]: item
        for item in state.get("result_columns", [])
        if item.get("result_name")
    }


def _build_result_columns(rows: list[dict[str, Any]], state: AgentState) -> list[dict[str, Any]]:
    """将声明契约与真实 rows、字段和指标元数据合并。

    真实 rows 决定最终返回哪些字段；result_columns 只提供字段来源和
    动态派生字段的声明。Meta 能确认的来源使用 matched，无法确认的
    动态字段保留 declared 或 unknown，不因别名相同而强行绑定基础指标。
    """
    column_candidates = _column_candidates(state.get("table_infos", []))
    metric_candidates = _metric_candidates(state.get("metric_infos", []))
    dimensions_by_column = _dimension_by_column(state.get("dimension_infos", []))
    declared = _declared_columns(state)
    result_columns = []

    for result_name in _result_keys(rows):
        declaration = declared.get(result_name, {})
        has_declaration = result_name in declared
        # 步骤 2.1：优先校验 LLM 声明的物理字段来源。
        source_column_id = declaration.get("source_column_id", "")
        column = None
        if source_column_id:
            matches = [
                item
                for item in column_candidates.get(source_column_id, [])
                if item.get("column_id") == source_column_id
            ]
            if len(matches) == 1:
                column = matches[0]
            else:
                source_column_id = ""
        # 有声明时必须尊重声明，不能因为结果别名恰好与物理字段同名就重新猜来源。
        if column is None and not has_declaration:
            matches = column_candidates.get(result_name, [])
            # 同名指标和物理字段存在歧义时不自动绑定物理字段。
            if len(matches) == 1 and result_name not in metric_candidates:
                column = matches[0]
                source_column_id = column["column_id"]

        # 步骤 2.2：校验 LLM 声明的指标来源，避免动态字段误绑定同名指标。
        source_metric_id = declaration.get("source_metric_id", "")
        metric = metric_candidates.get(source_metric_id) if source_metric_id else None
        if metric is None and not has_declaration:
            column_matches = column_candidates.get(result_name, [])
            metric = metric_candidates.get(result_name)
            # 没有字段声明时，只对不与物理字段重名的指标做名称兜底；
            # 例如 COUNT(DISTINCT order_id) AS order_count 不能被猜成原始订单量指标。
            if metric is not None and not column_matches:
                source_metric_id = metric["metric_id"]
            else:
                metric = None
                source_metric_id = ""
        elif metric is None and source_metric_id:
            source_metric_id = ""

        if has_declaration and declaration.get("source_metric_id") and metric is None:
            # 声明了不存在的指标时清空来源，避免把非法来源带到结果契约。
            source_metric_id = ""

        # 步骤 2.3：根据已确认的物理字段补齐业务维度信息。
        dimension = None
        if column is not None:
            matches = dimensions_by_column.get(column["column_id"], [])
            if len(matches) == 1:
                dimension = matches[0]

        # 步骤 2.4：确定字段角色、展示名称来源和血缘状态。
        if declaration.get("field_role") == "derived_metric":
            field_role = "derived_metric"
            display_name = declaration.get("display_name", "") or result_name
            display_name_source = (
                "llm_declared" if declaration.get("display_name") else "raw"
            )
            lineage_status = "declared"
        elif metric is not None and column is None:
            field_role = "metric"
            display_name = metric.get("business_name", "")
            display_name_source = "meta"
            lineage_status = "matched"
        elif dimension is not None or (column is not None and column.get("semantic_role") == "dimension"):
            field_role = "dimension"
            display_name = dimension.get("business_name", "") if dimension else column.get("business_name", "")
            display_name_source = "meta"
            lineage_status = "matched"
        elif column is not None:
            field_role = "unknown"
            display_name = column.get("business_name", "")
            display_name_source = "meta"
            lineage_status = "matched"
        else:
            field_role = declaration.get("field_role", "unknown")
            display_name = declaration.get("display_name", "")
            display_name_source = (
                "llm_declared" if display_name else "raw"
            )
            lineage_status = (
                "declared" if field_role != "unknown" else "unknown"
            )

        # 步骤 2.5：形成一个与真实返回字段一一对应的结构化结果字段。
        result_column = ResultColumn(
            result_name=result_name,
            field_role=field_role,
            display_name=display_name or result_name,
            source_column_id=source_column_id,
            source_metric_id=source_metric_id,
            source_fields=list(declaration.get("source_fields", [])),
            filter_conditions=list(declaration.get("filter_conditions", [])),
            display_name_source=display_name_source,
            lineage_status=lineage_status,
        ).model_dump()
        if dimension is not None:
            result_column.update({
                "dimension_id": dimension["dimension_id"],
                "dimension_business_name": dimension["business_name"],
            })
        if column is not None:
            result_column["column_name"] = column["column_name"]
        if metric is not None:
            result_column.update({
                "metric_name": metric["metric_name"],
                "metric_business_name": metric["business_name"],
                "unit": metric.get("unit"),
            })
        result_columns.append(result_column)
    return result_columns


def _build_mapping_requests(
    rows: list[dict[str, Any]],
    result_columns: list[dict[str, Any]],
) -> tuple[list[tuple[str, str]], dict[tuple[str, str], list[str]]]:
    """提取维度字段的批量查询键，并记录每个结果字段的值。

    只为已经确认是 dimension 且有 source_column_id 的字段查询维度值；
    指标和动态派生字段不进入维度值映射流程。
    """
    rows_by_key: dict[tuple[str, str], list[str]] = defaultdict(list)
    for result_column in result_columns:
        if result_column.get("field_role") != "dimension":
            continue
        column_id = result_column.get("source_column_id", "")
        if not column_id:
            continue
        result_name = result_column["result_name"]
        for row in rows:
            value = row.get(result_name)
            if value is None:
                continue
            raw_value = str(value)
            if result_name not in rows_by_key[(column_id, raw_value)]:
                rows_by_key[(column_id, raw_value)].append(result_name)
    return list(rows_by_key), rows_by_key


def _build_display_result(
    rows: list[dict[str, Any]],
    mappings: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """生成展示副本，保持 sql_result 原始行不变。"""
    display_by_key = {
        (item["result_name"], item["raw_value"]): item["display_name"]
        for item in mappings
        if item.get("status") == "mapped"
    }
    display_rows = []
    for row in rows:
        display_row = dict(row)
        for key, value in row.items():
            if value is not None:
                display_row[key] = display_by_key.get((key, str(value)), value)
        display_rows.append(display_row)
    return display_rows


async def enrich_query_result(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> dict[str, Any]:
    """在 SQL 执行后补齐结果字段、指标名称和维度值展示映射。"""
    writer = runtime.stream_writer
    step = "增强查询结果"
    writer({"type": "progress", "step": step, "node": "enrich_query_result", "status": "running"})

    # 步骤 1：以 SQL 执行返回的真实 rows 为准，不能以 LLM 声明替代真实字段。
    rows = state.get("sql_result", [])

    # 步骤 2：合并字段声明、真实结果和已召回的 Meta 字段/指标/维度信息。
    result_columns = _build_result_columns(rows, state)

    # 步骤 3：收集维度字段中的原始值，准备按 (column_id, raw_value) 批量查询。
    requests, rows_by_key = _build_mapping_requests(rows, result_columns)
    if requests:
        repository = runtime.context["meta_catalog_repository"]
        # 步骤 4.1：先识别已配置维度值目录的字段；没有目录的结构化维度
        # 直接保留原值，不产生“缺少映射”的误报。
        column_ids = list(dict.fromkeys(column_id for column_id, _ in requests))
        mapped_column_ids = await repository.get_dimension_value_column_ids(
            column_ids
        )
        mapped_column_id_set = set(mapped_column_ids)
        # 步骤 4.2：只查询已进入维度值目录的字段和值，避免无效的 Meta 查询。
        mapped_requests = [
            request for request in requests if request[0] in mapped_column_id_set
        ]
        meta_values = await repository.get_dimension_values_by_raw_values(
            mapped_requests
        )
    else:
        meta_values = []
        mapped_column_id_set = set()
    meta_by_key = {
        (item["column_id"], str(item["raw_value"])): item
        for item in meta_values
    }

    # 步骤 5：把 Meta 命中的值整理成可追溯映射记录；原始值始终保留。
    mappings = []
    for key, result_names in rows_by_key.items():
        column_id, raw_value = key
        if column_id not in mapped_column_id_set:
            continue
        item = meta_by_key.get(key)
        for result_name in result_names:
            mappings.append(
                {
                    "result_name": result_name,
                    "column_id": column_id,
                    "raw_value": raw_value,
                    "display_name": item["display_name"] if item else raw_value,
                    "dimension_id": item.get("dimension_id", "") if item else "",
                    "dimension_business_name": (
                        item.get("dimension_business_name", "") if item else ""
                    ),
                    "status": "mapped" if item else "unmapped",
                    "mapping_source": "meta" if item else "raw",
                }
            )

    # 步骤 6.1：基于映射记录生成展示副本，不修改 sql_result。
    display_rows = _build_display_result(rows, mappings)

    # 步骤 6.2：记录未命中的展示映射，交给上层决定是否进行后续语义补全。
    mapping_limitations = list(
        dict.fromkeys(
            f"字段 {item['result_name']} 的值 {item['raw_value']} 缺少展示名称映射。"
            for item in mappings
            if item["status"] == "unmapped"
        )
    )
    # 步骤 6.3：组装节点输出，供单一查询直接返回或分析任务写入 TaskResult。
    result = {
        "result_columns": result_columns,
        "dimension_value_mappings": mappings,
        "display_sql_result": display_rows,
        "mapping_limitations": mapping_limitations,
    }
    writer(
        {
            "type": "query_result_enriched",
            "step": step,
            "node": "enrich_query_result",
            "status": "success",
            **result,
        }
    )
    return result
