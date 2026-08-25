"""Agent 合并阶段使用的 Meta MySQL 元数据仓储。"""

from collections.abc import Sequence

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.entities.agent.agent_merge_context import MetricDimensionInfo, RelationshipInfo
from app.entities.meta.meta_columns import MetaColumns
from app.entities.meta.meta_dimensions import MetaDimensions
from app.entities.meta.meta_tables import MetaTables
from app.models.meta import (
    MetaColumnModel,
    MetaDimensionModel,
    MetaDimensionValueModel,
    MetaMetricDimensionModel,
    MetaRelationshipModel,
    MetaTableModel,
)
from app.repositories.mysql.meta.mappers.meta_column_mapper import MetaColumnMapper
from app.repositories.mysql.meta.mappers.meta_context_mapper import MetaContextMapper
from app.repositories.mysql.meta.mappers.meta_dimension_mapper import (
    MetaDimensionMapper,
)
from app.repositories.mysql.meta.mappers.meta_table_mapper import MetaTableMapper


class MetaCatalogRepository:
    """批量读取合并阶段需要的结构化元数据。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_tables_by_ids(self, table_ids: Sequence[str]) -> list[MetaTables]:
        if not table_ids:
            return []
        statement = select(MetaTableModel).where(
            MetaTableModel.status == "active",
            MetaTableModel.table_id.in_(list(table_ids)),
        ).order_by(MetaTableModel.table_id)
        models = (await self.session.scalars(statement)).all()
        return [MetaTableMapper.to_entity(model) for model in models]

    async def get_columns_by_ids(self, column_ids: Sequence[str]) -> list[MetaColumns]:
        if not column_ids:
            return []
        statement = select(MetaColumnModel).where(
            MetaColumnModel.status == "active",
            MetaColumnModel.is_queryable.is_(True),
            MetaColumnModel.column_id.in_(list(column_ids)),
        ).order_by(MetaColumnModel.column_id)
        models = (await self.session.scalars(statement)).all()
        return [MetaColumnMapper.to_entity(model) for model in models]

    async def get_queryable_columns_by_table_ids(self, table_ids: Sequence[str]) -> list[MetaColumns]:
        if not table_ids:
            return []
        statement = select(MetaColumnModel).where(
            MetaColumnModel.status == "active",
            MetaColumnModel.is_queryable.is_(True),
            MetaColumnModel.table_id.in_(list(table_ids)),
        ).order_by(MetaColumnModel.table_id, MetaColumnModel.column_id)
        models = (await self.session.scalars(statement)).all()
        return [MetaColumnMapper.to_entity(model) for model in models]

    async def get_relationships_by_table_ids(self, table_ids: Sequence[str]) -> list[RelationshipInfo]:
        if not table_ids:
            return []
        ids = list(table_ids)
        statement = select(MetaRelationshipModel).where(
            MetaRelationshipModel.from_table_id.in_(ids),
            MetaRelationshipModel.to_table_id.in_(ids),
        ).order_by(MetaRelationshipModel.relationship_id)
        models = (await self.session.scalars(statement)).all()
        return [MetaContextMapper.relationship_to_entity(model) for model in models]

    async def get_dimensions_by_column_ids(
        self, column_ids: Sequence[str]
    ) -> list[MetaDimensions]:
        """查询召回字段对应的完整业务维度。"""
        if not column_ids:
            return []
        statement = (
            select(MetaDimensionModel)
            .join(MetaColumnModel, (MetaColumnModel.table_id == MetaDimensionModel.table_id) & (MetaColumnModel.column_name == MetaDimensionModel.column_name))
            .where(MetaColumnModel.column_id.in_(list(column_ids)))
            .distinct()
            .order_by(MetaDimensionModel.dimension_id)
        )
        models = (await self.session.scalars(statement)).all()
        return [MetaDimensionMapper.to_entity(model) for model in models]

    async def get_metric_dimension_infos(self, metric_ids: Sequence[str], dimension_ids: Sequence[str]) -> list[MetricDimensionInfo]:
        if not metric_ids or not dimension_ids:
            return []
        statement = select(MetaMetricDimensionModel).where(
            MetaMetricDimensionModel.metric_id.in_(list(metric_ids)),
            MetaMetricDimensionModel.dimension_id.in_(list(dimension_ids)),
        ).order_by(MetaMetricDimensionModel.metric_id, MetaMetricDimensionModel.dimension_id)
        models = (await self.session.scalars(statement)).all()
        return [MetaContextMapper.metric_dimension_to_entity(model) for model in models]

    async def get_dimension_values_by_raw_values(
        self,
        value_keys: Sequence[tuple[str, str]],
    ) -> list[dict]:
        """按字段 ID 和数据库真实值批量读取展示名称。"""
        if not value_keys:
            return []
        statement = (
            select(
                MetaDimensionValueModel.value_id,
                MetaDimensionValueModel.dimension_id,
                MetaDimensionValueModel.column_id,
                MetaDimensionValueModel.raw_value,
                MetaDimensionValueModel.normalized_value,
                MetaDimensionValueModel.display_name,
                MetaDimensionModel.business_name.label("dimension_business_name"),
                MetaDimensionModel.column_name,
            )
            .join(
                MetaDimensionModel,
                MetaDimensionModel.dimension_id
                == MetaDimensionValueModel.dimension_id,
            )
            .where(
                MetaDimensionValueModel.status == "active",
                tuple_(
                    MetaDimensionValueModel.column_id,
                    MetaDimensionValueModel.raw_value,
                ).in_(list(value_keys)),
            )
            .order_by(
                MetaDimensionValueModel.column_id,
                MetaDimensionValueModel.raw_value,
            )
        )
        rows = (await self.session.execute(statement)).mappings().all()
        return [dict(row) for row in rows]

    async def get_dimension_value_column_ids(
        self,
        column_ids: Sequence[str],
    ) -> list[str]:
        """返回已经配置维度值目录的字段 ID。"""
        if not column_ids:
            return []
        statement = (
            select(MetaDimensionValueModel.column_id)
            .where(
                MetaDimensionValueModel.status == "active",
                MetaDimensionValueModel.column_id.in_(list(column_ids)),
            )
            .distinct()
            .order_by(MetaDimensionValueModel.column_id)
        )
        return list(await self.session.scalars(statement))
