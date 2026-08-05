"""Meta 表 ORM 模型映射。"""

from app.entities.meta.meta_tables import MetaTables
from app.models.meta.meta_tables import MetaTableModel


class MetaTableMapper:
    @staticmethod
    def to_entity(model: MetaTableModel) -> MetaTables:
        return MetaTables(
            table_id=model.table_id,
            data_source_id=model.data_source_id,
            database_name=model.database_name,
            table_name=model.table_name,
            table_type=model.table_type,
            business_name=model.business_name,
            grain=model.grain,
            description=model.description,
            aliases=list(model.aliases or []),
            status=model.status,
        )
