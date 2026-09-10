"""独立的 Agent 记忆层。

本包实现四类记忆的存储和检索边界。本轮结束后的长期记忆形成已经由
AgentService 调用；下一轮的记忆召回与上下文组装仍由后续 ContextEngine 接入。
"""

from app.agent.memory.enums import MemoryScope, MemoryStatus, MemoryType
from app.agent.memory.factory import MemoryRuntime, build_memory_runtime
from app.agent.memory.interfaces import (
    MemoryAsset,
    MemoryContextReader,
    MemoryRecord,
    MemorySearchResult,
    MemorySource,
)
from app.agent.memory.manager import MemoryManager
from app.agent.memory.types.working import create_working_state_loader

__all__ = [
    "MemoryAsset",
    "MemoryContextReader",
    "MemoryManager",
    "MemoryRecord",
    "MemoryRuntime",
    "MemoryScope",
    "MemorySearchResult",
    "MemorySource",
    "MemoryStatus",
    "MemoryType",
    "build_memory_runtime",
    "create_working_state_loader",
]
