"""`meta` 数据库的底层 SQL 查询模块。

Repository 层尽量只负责写 SQL 和返回普通 Python 数据；业务处理和接口返回校验
放到 service/schema 层。
"""

from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session


def list_tables(db: Session) -> list[dict[str, Any]]:
    """查询登记在 `meta.tables` 中的 DW 表元数据。"""
    result = db.execute(
        text(
            """
            SELECT
                *
            FROM meta.tables
            ORDER BY table_type, table_name
            """
        )
    )
    return [dict(row._mapping) for row in result]


def list_active_tables_for_embedding(db: Session) -> list[dict[str, Any]]:
    """查询需要写入 Qdrant 的启用状态表元数据。"""
    result = db.execute(
        text(
            """
            SELECT
                table_id,
                data_source_id,
                database_name,
                table_name,
                table_type,
                business_name,
                grain,
                description,
                aliases,
                status
            FROM meta.tables
            WHERE status = 'active'
            ORDER BY table_id
            """
        )
    )
    return [dict(row._mapping) for row in result]


def list_active_columns_for_embedding(db: Session) -> list[dict[str, Any]]:
    """查询需要写入 Qdrant 的启用且可查询字段元数据。"""
    result = db.execute(
        text(
            """
            SELECT
                c.column_id,
                c.table_id,
                t.table_name,
                c.column_name,
                c.business_name,
                c.data_type,
                c.semantic_role,
                c.is_queryable,
                c.is_aggregatable,
                c.description,
                c.aliases,
                c.status
            FROM meta.columns c
            JOIN meta.tables t ON c.table_id = t.table_id
            WHERE c.status = 'active'
              AND c.is_queryable = 1
            ORDER BY c.column_id
            """
        )
    )
    return [dict(row._mapping) for row in result]


def list_active_metrics_for_embedding(db: Session) -> list[dict[str, Any]]:
    """查询需要写入 Qdrant 的启用状态指标元数据。"""
    result = db.execute(
        text(
            """
            SELECT
                metric_id,
                metric_name,
                business_name,
                base_table_id,
                expression_sql,
                aggregation_type,
                calculation_grain,
                aggregation_rule,
                unit,
                description,
                aliases,
                status
            FROM meta.metrics
            WHERE status = 'active'
            ORDER BY metric_id
            """
        )
    )
    return [dict(row._mapping) for row in result]


def list_active_dimension_values(
    db: Session,
    dimension_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    """查询需要同步到 ES 和 Qdrant 的启用状态维度值。"""
    if not dimension_ids:
        return []

    statement = text(
        """
        SELECT
            dv.value_id,
            dv.dimension_id,
            d.dimension_name,
            d.business_name AS dimension_business_name,
            dv.column_id,
            c.column_name,
            c.data_type,
            d.table_id,
            t.table_name,
            t.database_name,
            dv.raw_value,
            dv.normalized_value,
            dv.display_name,
            dv.aliases,
            dv.description,
            dv.value_count,
            dv.semantic_enabled,
            dv.status
        FROM meta.dimension_values AS dv
        JOIN meta.dimensions AS d ON d.dimension_id = dv.dimension_id
        JOIN meta.columns AS c ON c.column_id = dv.column_id
        JOIN meta.tables AS t ON t.table_id = d.table_id
        WHERE dv.status = 'active'
          AND dv.dimension_id IN :dimension_ids
        ORDER BY dv.dimension_id, dv.raw_value
        """
    ).bindparams(bindparam("dimension_ids", expanding=True))
    result = db.execute(statement, {"dimension_ids": dimension_ids})
    return [dict(row._mapping) for row in result]
