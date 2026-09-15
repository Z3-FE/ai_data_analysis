"""报告构建工具。"""

from .contracts import BuildReportInput, BuildReportOutput, BuildReportPort
from .tool import BuildReportTool

__all__ = [
    "BuildReportInput",
    "BuildReportOutput",
    "BuildReportPort",
    "BuildReportTool",
]
