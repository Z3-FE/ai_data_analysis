"""根据过滤后的额外上下文生成 SQL。

当前节点只负责生成 SQL 文本，不负责 SQL AST 校验、只读校验、执行或重试。
这些逻辑留到后续 SQL 闭环阶段实现。
"""

import json
import logging
from typing import Any

from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime

from app.agent.context import AgentContext
from app.agent.prompts.prompt_loader import load_prompt
from app.agent.result_schema import ResultColumn, SqlGenerationResult
from app.agent.state import AgentState
from app.agent.utils.timeout_record import record_llm_timeout
from app.agent.utils.stream_timeout import astream_with_idle_timeout
from app.core.config import settings

logger = logging.getLogger(__name__)


def _clean_sql(sql: str) -> str:
    """去掉模型偶尔附带的 Markdown 代码块和语言标记。"""
    cleaned = sql.strip()
    if cleaned.startswith("```") and cleaned.endswith("```"):
        cleaned = cleaned[3:-3].strip()
        if cleaned.lower().startswith("sql"):
            cleaned = cleaned[3:].strip()
        elif cleaned.lower().startswith("json"):
            cleaned = cleaned[4:].strip()
    return cleaned


def _parse_generation_result(raw_output: str) -> dict[str, Any]:
    """解析 SQL 和逐列字段契约，避免单列错误丢弃全部字段。"""
    cleaned = _clean_sql(raw_output)
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return {"sql": cleaned, "result_columns": []}

    if not isinstance(payload, dict):
        return {"sql": cleaned, "result_columns": []}

    sql = payload.get("sql", "")
    try:
        validated_sql = SqlGenerationResult(
            sql=sql,
            result_columns=[],
        ).sql
    except Exception:
        logger.warning("SQL 结构化输出无效，按纯 SQL 兼容处理：%r", raw_output)
        return {"sql": cleaned, "result_columns": []}

    result_columns = []
    raw_columns = payload.get("result_columns", [])
    if not isinstance(raw_columns, list):
        logger.warning("SQL 字段契约不是数组，忽略字段声明：%r", raw_columns)
        raw_columns = []
    for raw_column in raw_columns:
        # LLM 常用 JSON null 表示“该来源不适用”，但字段契约统一使用
        # 空字符串表示没有对应来源；先规范化可选来源，避免丢弃其他正确字段。
        if isinstance(raw_column, dict):
            raw_column = {
                **raw_column,
                "source_column_id": raw_column.get("source_column_id") or "",
                "source_metric_id": raw_column.get("source_metric_id") or "",
            }
        try:
            column = ResultColumn.model_validate(raw_column)
            # 这里仍是 LLM 的任务级声明，只有执行后的 Meta 校验才能把
            # lineage_status 提升为 matched。
            column = column.model_copy(
                update={
                    "display_name_source": (
                        "llm_declared" if column.display_name else "raw"
                    ),
                    "lineage_status": (
                        "declared"
                        if column.field_role != "unknown"
                        else "unknown"
                    ),
                }
            )
            result_columns.append(column.model_dump())
            continue
        except Exception as exc:
            logger.warning("SQL 单字段契约无效，尝试降级保留：%r，错误=%s", raw_column, exc)

        # filter_conditions 是可选的字段级说明。模型把整条 SQL 的 WHERE
        # 值写成字符串时，只丢弃这个属性，不能连带丢失字段角色和展示名称。
        if isinstance(raw_column, dict):
            recovered_column = dict(raw_column)
            recovered_column["filter_conditions"] = []
            try:
                column = ResultColumn.model_validate(recovered_column)
                column = column.model_copy(
                    update={
                        "display_name_source": (
                            "llm_declared" if column.display_name else "raw"
                        ),
                        "lineage_status": (
                            "declared"
                            if column.field_role != "unknown"
                            else "unknown"
                        ),
                    }
                )
                result_columns.append(column.model_dump())
                continue
            except Exception as recovery_exc:
                logger.warning("SQL 字段契约属性级降级失败：%r，错误=%s", raw_column, recovery_exc)

        # 其他属性仍不合法时，只要字段名可信就保留最小契约；来源和角色
        # 交给执行后的结果增强节点根据真实 rows 与 Meta 继续补齐。
        result_name = (
            raw_column.get("result_name", "")
            if isinstance(raw_column, dict)
            else ""
        )
        if not isinstance(result_name, str) or not result_name.strip():
            continue
        display_name = raw_column.get("display_name", "")
        if not isinstance(display_name, str):
            display_name = ""
        result_columns.append(
            ResultColumn(
                result_name=result_name,
                field_role="unknown",
                display_name=display_name,
                display_name_source=(
                    "llm_declared" if display_name else "raw"
                ),
            ).model_dump()
        )

    return {"sql": _clean_sql(validated_sql), "result_columns": result_columns}


async def generate_sql(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> dict[str, Any]:
    """把用户问题和额外上下文交给 LLM，返回生成的 SQL。"""
    writer = runtime.stream_writer
    step = "生成 SQL"
    writer({"type": "progress", "step": step, "node": "generate_sql", "status": "running"})

    prompt = PromptTemplate(
        template=load_prompt("generate_sql"),
        input_variables=["query", "extra_context"],
    )
    llm_client = runtime.context["llm_client"]
    auto_stream = getattr(llm_client, "astream_auto", None)
    chain = None if auto_stream is not None else prompt | llm_client | StrOutputParser()
    input_values = {
        "query": state.get("input_text", ""),
        "extra_context": json.dumps(
            state.get("extra_context", {}),
            ensure_ascii=False,
            indent=2,
        ),
    }
    timeout_seconds = runtime.context.get(
        "llm_timeout_seconds", settings.llm.timeout_seconds
    )
    logger.info(
        "开始调用 SQL 生成 LLM：timeout_seconds=%s，问题=%r，上下文字符数=%s",
        timeout_seconds,
        input_values["query"],
        len(input_values["extra_context"]),
    )
    raw_parts: list[str] = []
    reasoning_parts: list[str] = []
    accumulated_chars = 0
    reasoning_chars = 0
    try:
        if auto_stream is not None:
            # 正式客户端已经把不同模型的思考字段归一化成 AutoLLMEvent；
            # SQL 节点只消费 reasoning/content，不读取厂商原生字段。
            async for event in astream_with_idle_timeout(
                auto_stream(prompt.format(**input_values)),
                timeout_seconds,
            ):
                event_type = getattr(event, "event_type", "")
                event_text = getattr(event, "text", "") or ""
                if event_type == "reasoning" and event_text:
                    reasoning_parts.append(event_text)
                    reasoning_chars += len(event_text)
                    writer(
                        {
                            "type": "reasoning_chunk",
                            "step": step,
                            "node": "generate_sql",
                            "chunk": event_text,
                            "chunk_index": len(reasoning_parts) - 1,
                            "accumulated_chars": reasoning_chars,
                        }
                    )
                elif event_type == "content" and event_text:
                    raw_parts.append(event_text)
                    accumulated_chars += len(event_text)
                    writer(
                        {
                            "type": "llm_chunk",
                            "step": step,
                            "node": "generate_sql",
                            "chunk": event_text,
                            "chunk_index": len(raw_parts) - 1,
                            "accumulated_chars": accumulated_chars,
                        }
                    )
        else:
            # 普通 Runnable 和单元测试客户端仍可走 LangChain 原生流。
            assert chain is not None
            async for chunk in astream_with_idle_timeout(
                chain.astream(input_values),
                timeout_seconds,
            ):
                if not isinstance(chunk, str):
                    chunk = str(chunk)
                if not chunk:
                    continue
                raw_parts.append(chunk)
                accumulated_chars += len(chunk)
                writer(
                    {
                        "type": "llm_chunk",
                        "step": step,
                        "node": "generate_sql",
                        "chunk": chunk,
                        "chunk_index": len(raw_parts) - 1,
                        "accumulated_chars": accumulated_chars,
                    }
                )
    except TimeoutError as exc:
        message = f"SQL 生成超时（连续 {timeout_seconds:g} 秒没有返回）。"
        logger.debug("SQL 生成流式空闲超时：%s", exc)
        logger.error(message)
        record_llm_timeout(
            node="generate_sql",
            step=step,
            call_mode="stream",
            timeout_kind="stream_idle",
            timeout_seconds=timeout_seconds,
            error=message,
            llm_client=llm_client,
            writer=writer,
        )
        writer(
            {
                "type": "generate_sql",
                "step": step,
                "node": "generate_sql",
                "status": "failed",
                "error": message,
            }
        )
        raise TimeoutError(message) from exc
    except Exception as exc:
        logger.exception("SQL 生成失败")
        writer(
            {
                "type": "generate_sql",
                "step": step,
                "node": "generate_sql",
                "status": "failed",
                "error": str(exc),
            }
        )
        raise
    raw_output = "".join(raw_parts)
    if not isinstance(raw_output, str) or not raw_output.strip():
        message = "SQL 生成 LLM 返回为空。"
        logger.error("%s 原始返回类型=%s", message, type(raw_output).__name__)
        writer(
            {
                "type": "generate_sql",
                "step": step,
                "node": "generate_sql",
                "status": "failed",
                "error": message,
            }
        )
        raise ValueError(message)

    logger.info(
        "SQL 生成 LLM 返回：思考字符数=%s，结果字符数=%s，原始内容=%r",
        reasoning_chars,
        len(raw_output),
        raw_output,
    )
    reasoning_output = "".join(reasoning_parts)
    if reasoning_output:
        writer(
            {
                "type": "reasoning_result",
                "step": step,
                "node": "generate_sql",
                "reasoning": reasoning_output,
                "characters": len(reasoning_output),
            }
        )
    writer(
        {
            "type": "llm_result",
            "step": step,
            "node": "generate_sql",
            "raw_result": raw_output,
        }
    )
    try:
        result = _parse_generation_result(raw_output)
    except Exception as exc:
        logger.exception("SQL 生成 LLM 返回解析失败：%r", raw_output)
        writer(
            {
                "type": "generate_sql",
                "step": step,
                "node": "generate_sql",
                "status": "failed",
                "error": f"SQL 生成结果解析失败：{exc}",
            }
        )
        raise
    logger.info(
        "SQL 生成结果解析完成：sql_chars=%s，result_columns=%s",
        len(result["sql"]),
        len(result["result_columns"]),
    )
    writer(
        {
                "type": "generate_sql",
                "step": step,
                "node": "generate_sql",
                "status": "success",
            **result,
        }
    )
    return {**result, "sql_reasoning": reasoning_output}
