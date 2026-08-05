"""Meta 字段 ORM 模型映射。"""

from app.entities.meta.meta_columns import MetaColumns
from app.models.meta.meta_columns import MetaColumnModel


class MetaColumnMapper:
    @staticmethod
    def to_entity(model: MetaColumnModel) -> MetaColumns:
        return MetaColumns(
            column_id=model.column_id,
            table_id=model.table_id,
            column_name=model.column_name,
            business_name=model.business_name,
            data_type=model.data_type,
            semantic_role=model.semantic_role,
            is_queryable=bool(model.is_queryable),
            is_aggregatable=bool(model.is_aggregatable),
            description=model.description,
            aliases=list(model.aliases or []),
            status=model.status,
        )
