"""从 AgentState 提取用户可见的轮次输出。"""

from typing import Any

from app.agent.state import AgentState


def build_turn_output(result: AgentState) -> tuple[str, dict[str, Any], str]:
    """返回输出类型、可重渲染载荷和适合进入消息历史的文本。"""
    if result.get("execution_mode") == "daily_chat":
        content = result.get("output_text") or result.get("llm_output")
        if content:
            content = str(content)
            return "text", {"message": content}, content

    report = result.get("rendered_report")
    if isinstance(report, dict) and report:
        content = str(report.get("summary") or report.get("title") or "报告已生成。")
        return "rendered_report", report, content

    if result.get("execution_mode") == "clarification":
        content = str(
            result.get("clarification_question")
            or result.get("output_text")
            or "请补充问题中的关键指标或范围。"
        )
        return "clarification", {"message": content}, content

    rows = result.get("display_sql_result") or result.get("sql_result") or []
    if result.get("execution_mode") == "single_query":
        max_rows = 200
        payload = {
            "columns": result.get("result_columns", []),
            "rows": rows[:max_rows],
            "row_count": len(rows),
            "truncated": len(rows) > max_rows,
        }
        return (
            "query_result",
            payload,
            str(result.get("output_text") or "查询已完成。"),
        )

    content = result.get("output_text") or result.get("llm_output")
    if content:
        content = str(content)
        return "text", {"message": content}, content

    error = str(result.get("report_plan_error") or "Agent 执行失败。")
    return "failure", {"message": error}, error


__all__ = ["build_turn_output"]
