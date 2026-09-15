"""独立的 Agent 记忆层。

本包实现四类记忆的存储和检索边界。Harness 的 ContextEngine 从本包读取当前
会话消息和长期记忆；本轮结束后的长期记忆形成由 Harness Finalization 触发。
"""

from typing import Any

_LAZY_EXPORTS = {
    "MemoryAsset": ("app.agent.memory.interfaces", "MemoryAsset"),
    "MemoryContextReader": ("app.agent.memory.interfaces", "MemoryContextReader"),
    "MemoryManager": ("app.agent.memory.manager", "MemoryManager"),
    "MemoryRecord": ("app.agent.memory.interfaces", "MemoryRecord"),
    "MemoryRuntime": ("app.agent.memory.factory", "MemoryRuntime"),
    "MemoryScope": ("app.agent.memory.enums", "MemoryScope"),
    "MemorySearchResult": ("app.agent.memory.interfaces", "MemorySearchResult"),
    "MemorySource": ("app.agent.memory.interfaces", "MemorySource"),
    "MemoryStatus": ("app.agent.memory.enums", "MemoryStatus"),
    "MemoryType": ("app.agent.memory.enums", "MemoryType"),
    "build_memory_runtime": ("app.agent.memory.factory", "build_memory_runtime"),
}


def __getattr__(name: str) -> Any:
    """按需加载公开对象，避免导入子模块时提前初始化整个运行时。"""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(target[0])
    value = getattr(module, target[1])
    globals()[name] = value
    return value

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
]
