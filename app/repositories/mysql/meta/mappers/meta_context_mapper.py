"""Meta 合并上下文 ORM 模型映射。"""

from app.entities.agent.agent_merge_context import MetricDimensionInfo, RelationshipInfo
from app.models.meta.meta_metric_dimensions import MetaMetricDimensionModel
from app.models.meta.meta_relationships import MetaRelationshipModel


class MetaContextMapper:
    @staticmethod
    def relationship_to_entity(model: MetaRelationshipModel) -> RelationshipInfo:
        return RelationshipInfo(
            relationship_id=model.relationship_id,
            from_table_id=model.from_table_id,
            from_column_name=model.from_column_name,
            to_table_id=model.to_table_id,
            to_column_name=model.to_column_name,
            relationship_type=model.relationship_type,
            description=model.description,
        )

    @staticmethod
    def metric_dimension_to_entity(
        model: MetaMetricDimensionModel,
    ) -> MetricDimensionInfo:
        return MetricDimensionInfo(
            metric_id=model.metric_id,
            dimension_id=model.dimension_id,
            compatibility_note=model.compatibility_note,
        )
