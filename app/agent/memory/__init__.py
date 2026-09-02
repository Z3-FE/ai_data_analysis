"""独立的 Agent 记忆层。

本包实现四类记忆的存储和检索边界，但当前阶段不自动接入 agent_graph。
上层上下文工程可以通过 MemoryManager 显式调用它。
"""

from app.agent.memory.enums import MemoryScope, MemoryStatus, MemoryType
from app.agent.memory.factory import MemoryRuntime, build_memory_runtime
from app.agent.memory.interfaces import (
    MemoryAsset,
    MemoryCreate,
    MemoryRecord,
    MemorySearchResult,
    MemorySource,
)
from app.agent.memory.manager import MemoryManager
from app.agent.memory.types.working import create_working_state_loader

__all__ = [
    "MemoryCreate",
    "MemoryAsset",
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
