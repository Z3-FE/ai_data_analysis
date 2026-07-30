"""`meta_metrics_semantic` 集合类型。"""

from dataclasses import dataclass

from app.entities.metrics import Metrics


@dataclass
class MetaMetricsSemanticPayload(Metrics):
    """`meta_metrics_semantic` 的扁平 payload 类型。"""

    vector_type: str = ""
    point_key: str = ""
    text: str = ""


@dataclass
class MetaMetricsSemantic:
    """`meta_metrics_semantic` 的命中结果类型。"""

    id: str
    score: float
    payload: MetaMetricsSemanticPayload
    vector: list[float] | None = None
