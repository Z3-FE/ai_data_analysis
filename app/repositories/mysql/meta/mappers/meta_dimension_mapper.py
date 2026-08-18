"""Meta 维度 ORM 模型映射。"""

from app.entities.meta.meta_dimensions import MetaDimensions
from app.models.meta.meta_dimensions import MetaDimensionModel


class MetaDimensionMapper:
    @staticmethod
    def to_entity(model: MetaDimensionModel) -> MetaDimensions:
        return MetaDimensions(
            dimension_id=model.dimension_id,
            dimension_name=model.dimension_name,
            business_name=model.business_name,
            table_id=model.table_id,
            column_name=model.column_name,
            description=model.description,
        )
