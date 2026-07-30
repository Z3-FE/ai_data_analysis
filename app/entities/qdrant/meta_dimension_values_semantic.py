"""`meta_dimension_values_semantic` 集合类型。"""

from dataclasses import dataclass

from app.entities.dimension_value import DimensionValueInfo


@dataclass(frozen=True)
class MetaDimensionValuesSemanticPayload(DimensionValueInfo):
    """`meta_dimension_values_semantic` 的扁平 payload 类型。"""

    vector_type: str = ""
    point_key: str = ""
    text: str = ""


@dataclass(frozen=True)
class MetaDimensionValuesSemantic:
    """`meta_dimension_values_semantic` 的命中结果类型。"""

    id: str
    score: float
    payload: MetaDimensionValuesSemanticPayload
    vector: list[float] | None = None
