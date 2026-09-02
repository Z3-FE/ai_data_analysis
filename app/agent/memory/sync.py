"""记忆索引同步的后台任务入口。"""

from app.agent.memory.lifecycle import MemoryLifecycle


async def run_memory_maintenance(lifecycle: MemoryLifecycle) -> dict[str, int]:
    """执行一次过期清理和索引重试，供定时任务或管理命令调用。"""
    expired = await lifecycle.expire()
    graph_result = await lifecycle.rebuild_pending_graph()
    retry_result = await lifecycle.retry_indexes()
    return {
        "expired": expired,
        "graph_completed": graph_result["completed"],
        "graph_failed": graph_result["failed"],
        "index_completed": retry_result["completed"],
        "index_failed": retry_result["failed"],
    }
