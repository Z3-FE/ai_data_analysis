"""按依赖关系执行分析计划中的查询与计算任务。

分析任务的职责边界：

分析计划
    -> 按依赖层选择可执行任务
    -> 使用前置计算结果具体化后续查询问题
    -> 调用不带问题路由的 Query Agent
    -> 根据完整 SQL 结果生成数据画像
    -> LLM 基于真实字段生成 calculate(rows)
    -> 在受限子进程中执行 Python
    -> 保存 TaskResult 并向外部流式发送任务事件

本节点只负责分析侧的编排和计算，不修改 Query Agent 内部的召回、SQL
生成与 SQL 执行逻辑。"""

import asyncio
import json
import logging
import re
from decimal import Decimal
from typing import Any, Literal

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from app.agent.context import AgentContext
from app.clients.auto_llm_client import extract_content, extract_reasoning
from app.agent.prompts.prompt_loader import load_prompt
from app.agent.python_sandbox import execute_python_calculation
from app.agent.query_graph import query_graph
from app.agent.state import AgentState
from app.agent.utils.stream_timeout import astream_with_idle_timeout
from app.agent.utils.timeout_record import (
    classify_timeout,
    is_llm_timeout,
    record_llm_timeout,
)
from app.core.config import settings

logger = logging.getLogger(__name__)


class PythonCalculation(BaseModel):
    """LLM 基于真实查询字段生成的 Python 计算程序。

    ``code`` 只保存 calculate(rows) 函数源码，真正执行时会交给
    python_sandbox 做 AST 校验和子进程隔离。
    """

    # LLM 生成的 calculate(rows) 函数源码，沙箱会使用完整 SQL rows 执行。
    code: str = Field(
        min_length=1,
        description="只定义 calculate(rows) 函数的纯 Python 代码，不包含 Markdown。",
    )
    # 描述动态计算结果的字段和业务含义，供后续证据汇总与结论生成参考。
    result_description: str = Field(
        min_length=1, description="说明 calculate(rows) 返回结果的字段和含义。"
    )


class ResolvedTaskQuestion(BaseModel):
    """使用前置计算结果具体化后的 Query Agent 查询问题。

    Planner 只知道“目标月份”这类动态引用；这个模型承接解析结果，
    确保真正进入 Query Agent 的问题包含可执行的实际条件。
    """

    # 已经注入前置 calculation_result 中动态条件的实际查询问题。
    question: str = Field(
        min_length=1,
        description="包含真实查询条件、可以直接交给 Query Agent 的自然语言问题。",
    )


class TaskResult(BaseModel):
    """一个分析查询任务及其 Python 计算的执行结果。

    ``question`` 保留 Planner 原始任务，``resolved_question`` 保留依赖
    解析后的实际问题，便于调试“计划要求”和“实际查询”之间的差异。
    ``rows`` 是完整 SQL 结果，``data_profile`` 是传给 Python 生成器的受控摘要。
    """

    # 任务唯一标识；任务依赖、证据引用和展示产物都通过该 ID 建立关联。
    task_id: str
    # 当前任务的执行状态；失败任务仍保留已产生的执行信息用于排查。
    status: Literal["success", "failed"]
    # Analysis Planner 最初生成的查询问题，可能仍包含“目标月份”等动态引用。
    question: str
    # 当前查询在整体分析中的用途，以及查询完成后需要执行的计算目标。
    purpose: str
    # 当前任务依赖的前置任务 ID；调度器据此决定任务是否可以执行。
    depends_on: list[str] = Field(default_factory=list)
    # 把前置 calculation_result 注入动态条件后，真正交给 Query Agent 的问题。
    resolved_question: str = ""
    # Query Agent 最终执行的 SQL；当前用于审计和排查，并保留给未来报告生成参考。
    sql: str = ""
    # SQL 返回的完整原始数据；Python 计算和后续数据产物必须使用原始值。
    rows: list[dict] = Field(default_factory=list)
    # SQL 返回字段的数据统计摘要；当前与 data_profile["columns"] 内容相同。
    # 顶层保留便于任务结果直接查看，后续如去重应同步调整画像生成器的输入协议。
    columns: list[dict] = Field(default_factory=list)
    # 基于完整 rows 生成的数据画像，包含字段统计和受控代表行，供 LLM 生成 Python。
    # 其中 columns 是 LLM 理解字段类型所必需的内容，不能直接从画像中删除。
    data_profile: dict = Field(default_factory=dict)
    # LLM 生成并由 Python 沙箱实际执行的 calculate(rows) 函数源码。
    python_code: str = ""
    # 说明 Python 进行了什么计算，以及 calculation_result 中动态字段的含义。
    calculation_description: str = ""
    # Python 计算得到的核心事实；后续依赖任务和结论总结器主要消费该字段。
    calculation_result: Any = None
    # SQL 真实返回字段的语义契约，记录字段角色、展示名称、来源和血缘状态。
    result_columns: list[dict] = Field(default_factory=list)
    # 维度原始值到用户展示名称的可追溯映射，不覆盖 rows 中的数据库原始值。
    dimension_value_mappings: list[dict] = Field(default_factory=list)
    # 将维度值替换为展示名称后的结果副本，只用于表格、图表或报告展示。
    display_sql_result: list[dict] = Field(default_factory=list)
    # 记录已经配置映射目录但未找到展示名称的值，供结论和界面说明数据边界。
    mapping_limitations: list[str] = Field(default_factory=list)
    # 任务失败原因；成功任务保持空字符串。
    error: str = ""


class AnalysisEvidenceTask(BaseModel):
    """供证据汇总和后续总结使用的单任务精简证据。

    完整 rows、data_profile 和 Python 源码仍保留在 TaskResult 中，
    这里仅保留总结器判断结论所需的信息，避免重复传输大段明细。
    """

    # 对应完整 TaskResult 的任务 ID，用于结论引用和回查原始任务数据。
    task_id: str
    # 证据任务状态；只有 success 任务可以成为分析结论的事实来源。
    status: Literal["success", "failed"]
    # 说明该证据在原始问题中的分析用途。
    purpose: str
    # 保留任务依赖关系，说明当前证据是基于哪些前置计算结果产生的。
    depends_on: list[str] = Field(default_factory=list)
    # 真正执行的数据查询问题，帮助总结器理解 calculation_result 的业务范围。
    resolved_question: str = ""
    # 保留实际 SQL 用于审计和未来报告参考；当前文字总结器尚未把它传给 LLM。
    sql: str = ""
    # 完整 SQL rows 的数量；让总结器知道证据规模，但不传输完整明细。
    row_count: int = 0
    # 查询字段的数据统计摘要；当前主要用于证据追溯，文字总结器尚未消费。
    columns: list[dict] = Field(default_factory=list)
    # 对 calculation_result 的字段和计算逻辑进行自然语言说明。
    calculation_description: str = ""
    # 当前任务已经由 Python 算出的可靠事实，是总结器最核心的输入。
    calculation_result: Any = None
    # SQL 结果字段的语义说明，帮助总结器识别维度、指标及展示名称。
    result_columns: list[dict] = Field(default_factory=list)
    # 维度原始值与展示名称的映射，帮助总结器避免直接输出难懂的英文编码。
    dimension_value_mappings: list[dict] = Field(default_factory=list)
    # 当前任务未完成的维度值映射，最终会合并进分析限制。
    mapping_limitations: list[str] = Field(default_factory=list)
    # 失败任务的错误原因；成功任务保持空字符串。
    error: str = ""


class AnalysisEvidence(BaseModel):
    """全部分析任务完成后形成的结构化证据集合。

    该模型只负责保存分析计划摘要、任务结果和任务状态，不重新执行
    趋势、排名或贡献度计算，也不负责生成面向用户的自然语言结论。
    """

    # 用户最初提出的问题，限定后续总结必须回答的目标。
    original_question: str
    # Analysis Planner 对整体分析目标的概括，不包含尚未计算出的结论。
    analysis_summary: str
    # 全部任务的汇总状态：全部成功、部分成功或全部失败。
    status: Literal["success", "partial", "failed"]
    # 面向总结器的精简任务证据，不携带完整 rows、data_profile 和 Python 源码。
    # 图表和表格仍可通过同一 task_id 回到 analysis_task_results 读取完整数据。
    task_results: list[AnalysisEvidenceTask] = Field(default_factory=list)
    # 可以被分析结论引用的成功任务 ID。
    successful_task_ids: list[str] = Field(default_factory=list)
    # 执行失败的任务 ID，用于判断 partial/failed 和生成限制说明。
    failed_task_ids: list[str] = Field(default_factory=list)


def _build_data_profile(rows: list[dict]) -> dict[str, Any]:
    """基于完整查询结果生成受控的数据画像。

    画像必须基于完整 rows 统计，不能只根据少量样例推断字段分布；
    只有传给 LLM 的明细行需要受控，实际 Python 执行仍使用完整 rows。
    """
    # 保留 SQL 返回字段的首次出现顺序，避免画像字段顺序随集合操作变化。
    column_names = list(dict.fromkeys(key for row in rows for key in row))
    columns = []
    for name in column_names:
        # 字段级统计用于帮助 LLM 识别维度、指标、空值和数据范围。
        values = [row.get(name) for row in rows]
        non_null_values = [value for value in values if value is not None]
        distinct_values = list(
            {
                json.dumps(value, ensure_ascii=False, default=str, sort_keys=True): value
                for value in non_null_values
            }.values()
        )
        column = {
            "name": name,
            "types": sorted({type(value).__name__ for value in non_null_values}),
            "nullable": len(non_null_values) != len(values),
            "null_count": len(values) - len(non_null_values),
            "distinct_count": len(distinct_values),
        }
        # 数值字段提供范围；非数值字段提供稳定的代表性值。
        if non_null_values and all(
            isinstance(value, (int, float, Decimal))
            and not isinstance(value, bool)
            for value in non_null_values
        ):
            numeric_values = [float(value) for value in non_null_values]
            column.update({"min": min(numeric_values), "max": max(numeric_values)})
        elif distinct_values:
            if len(distinct_values) <= 5:
                sample_values = distinct_values
            else:
                last_index = len(distinct_values) - 1
                sample_values = [
                    distinct_values[index]
                    for index in sorted(
                        {0, last_index // 4, last_index // 2, 3 * last_index // 4, last_index}
                    )
                ]
            column["sample_values"] = sample_values
        columns.append(column)

    # 小结果集保留全部明细；大结果集只保留首尾和等距位置的代表行。
    if len(rows) <= 50:
        profile_rows = rows
        row_mode = "full"
    else:
        last_index = len(rows) - 1
        indexes = sorted(
            {round(index * last_index / 11) for index in range(12)}
        )
        profile_rows = [rows[index] for index in indexes]
        row_mode = "representative_sample"

    return {
        "row_count": len(rows),
        "columns": columns,
        "row_mode": row_mode,
        "profile_rows": profile_rows,
    }


def _normalize_calculation_result(value: Any) -> Any:
    """在计算结果离开沙箱前消除浮点展示噪声。

    这里不根据字段名称猜测金额、比例或其他业务含义；动态代码负责业务
    精度，后处理只把浮点二进制误差统一收敛到 6 位。
    """
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, list):
        return [_normalize_calculation_result(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _normalize_calculation_result(item)
            for key, item in value.items()
        }
    return value


def _validate_decrease_result(result: Any, task: dict[str, Any]) -> None:
    """防止下降分析在没有真实下降时返回金额为零的伪结果。

    只对明确要求下降或减少的任务进行检查；其他分析结果保持动态结构，
    不因为不存在 decrease_amount 字段而被错误拒绝。
    """
    task_text = f"{task['question']} {task['purpose']}"
    if not any(keyword in task_text for keyword in ("下降", "减少", "降低")):
        return

    decrease_amounts = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "decrease_amount" and isinstance(item, (int, float)):
                    decrease_amounts.append(item)
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(result)
    if not decrease_amounts:
        raise ValueError("下降分析结果缺少 decrease_amount 字段")
    if not any(amount > 0 for amount in decrease_amounts):
        raise ValueError("没有发现真实下降，不能返回下降金额为 0 的结果")


async def _stream_structured_llm(
    *,
    prompt: PromptTemplate,
    input_values: dict[str, Any],
    runtime: Runtime[AgentContext],
    writer: Any,
    step: str,
    task_id: str,
    phase: str,
) -> str:
    """流式获取结构化 LLM 输出，并保留思考与正文事件。

    依赖解析和 Python 代码生成原来通过 ``ainvoke`` 等待完整 JSON，
    在模型思考时间较长时前端只能收到心跳。正式 AutoLLM 客户端使用
    ``astream_auto``，测试或其他 LangChain Runnable 使用 ``prompt | llm``
    的原生 ``astream``；两条路径最终都返回完整字符串，再由调用方做
    Pydantic 校验，所以不会改变结构化输出协议。
    """
    llm_client = runtime.context["llm_client"]
    timeout_seconds = runtime.context.get(
        "llm_timeout_seconds", settings.llm.timeout_seconds
    )
    auto_stream = getattr(llm_client, "astream_auto", None)
    if auto_stream is not None:
        stream = auto_stream(prompt.format(**input_values))
    else:
        stream = (prompt | llm_client).astream(input_values)

    raw_parts: list[str] = []
    reasoning_parts: list[str] = []
    content_chars = 0
    reasoning_chars = 0
    async for chunk in astream_with_idle_timeout(stream, timeout_seconds):
        event_type = getattr(chunk, "event_type", "")
        if event_type:
            text = getattr(chunk, "text", "") or ""
            if event_type == "reasoning" and text:
                reasoning_parts.append(text)
                reasoning_chars += len(text)
                writer(
                    {
                        "type": "reasoning_chunk",
                        "step": step,
                        "node": "execute_analysis",
                        "phase": phase,
                        "task_id": task_id,
                        "chunk": text,
                        "chunk_index": len(reasoning_parts) - 1,
                        "accumulated_chars": reasoning_chars,
                    }
                )
            elif event_type == "content" and text:
                raw_parts.append(text)
                content_chars += len(text)
                writer(
                    {
                        "type": "llm_chunk",
                        "step": step,
                        "node": "execute_analysis",
                        "phase": phase,
                        "task_id": task_id,
                        "chunk": text,
                        "chunk_index": len(raw_parts) - 1,
                        "accumulated_chars": content_chars,
                    }
                )
            continue

        reasoning = extract_reasoning(chunk)
        if reasoning:
            reasoning_parts.append(reasoning)
            reasoning_chars += len(reasoning)
            writer(
                {
                    "type": "reasoning_chunk",
                    "step": step,
                    "node": "execute_analysis",
                    "phase": phase,
                    "task_id": task_id,
                    "chunk": reasoning,
                    "chunk_index": len(reasoning_parts) - 1,
                    "accumulated_chars": reasoning_chars,
                }
            )
        text = extract_content(chunk)
        if not text and isinstance(chunk, str):
            text = chunk
        if text:
            raw_parts.append(text)
            content_chars += len(text)
            writer(
                {
                    "type": "llm_chunk",
                    "step": step,
                    "node": "execute_analysis",
                    "phase": phase,
                    "task_id": task_id,
                    "chunk": text,
                    "chunk_index": len(raw_parts) - 1,
                    "accumulated_chars": content_chars,
                }
            )

    raw = "".join(raw_parts).strip()
    if not raw:
        raise ValueError(f"分析任务 LLM 返回为空：{task_id}/{phase}")
    if reasoning_parts:
        writer(
            {
                "type": "reasoning_result",
                "step": step,
                "node": "execute_analysis",
                "phase": phase,
                "task_id": task_id,
                "reasoning": "".join(reasoning_parts),
                "characters": reasoning_chars,
            }
        )
    writer(
        {
            "type": "llm_result",
            "step": step,
            "node": "execute_analysis",
            "phase": phase,
            "task_id": task_id,
            "raw_result": raw,
        }
    )
    return raw


def _parse_structured_output(parser: Any, raw: str) -> Any:
    """从模型混合输出中提取结构化 JSON，再交给 LangChain Parser 校验。

    思考型模型偶尔会把解释文字、Python 代码块和最终 JSON 一起放进
    content。直接调用 PydanticOutputParser 时，它可能优先尝试第一个
    Python 代码块，导致本来存在的最终 JSON 也无法被解析。
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def add_candidate(value: str) -> None:
        candidate = value.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    add_candidate(raw)

    fenced_blocks = re.findall(r"```([^\n`]*)\n(.*?)```", raw, flags=re.DOTALL)
    fenced_blocks.sort(
        key=lambda item: 0 if item[0].strip().lower().startswith("json") else 1
    )
    for _, body in fenced_blocks:
        add_candidate(body)

    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", raw):
        try:
            _, end = decoder.raw_decode(raw[match.start() :])
        except json.JSONDecodeError:
            continue
        add_candidate(raw[match.start() : match.start() + end])

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            return parser.parse(candidate)
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    raise ValueError("结构化 LLM 输出为空，无法解析")


async def _execute_task(
    task: dict[str, Any],
    state: AgentState,
    completed_results: dict[str, dict[str, Any]],
    runtime: Runtime[AgentContext],
) -> dict[str, Any]:
    """执行单个任务，并将前置结果用于具体化查询条件。

    失败会被包装为当前任务的 failed 结果并继续返回，后续依赖任务会
    根据该状态停止执行；当前版本不做 SQL 自动修正或重试。
    """
    writer = runtime.stream_writer
    step = f"执行分析任务：{task['task_id']}"
    writer({"type": "progress", "step": step, "node": "execute_analysis", "task_id": task["task_id"], "status": "running"})
    query_result = {}
    rows = []
    columns = []
    data_profile = {}
    calculation_code = ""
    calculation_description = ""
    resolved_question = task["question"]
    current_phase = "查询数据"

    try:
        # 依赖任务从 completed_results 读取前置结果
        dependency_results = [
            completed_results[task_id] for task_id in task["depends_on"]
        ]
        failed_dependencies = [
            result["task_id"]
            for result in dependency_results
            if result["status"] != "success"
        ]
        if failed_dependencies:
            raise ValueError(
                f"前置任务执行失败：{', '.join(failed_dependencies)}"
            )

        # 依赖任务只提供动态条件，例如目标月份和对比月份。
        if dependency_results:
            current_phase = "解析依赖结果"
            writer(
                {
                    "type": "analysis_task_phase",
                    "step": step,
                    "node": "execute_analysis",
                    "status": "running",
                    "task_id": task["task_id"],
                    "phase": "解析依赖结果",
                    "message": "正在等待 LLM 解析前置任务结果。",
                }
            )
            parser = PydanticOutputParser(pydantic_object=ResolvedTaskQuestion)
            prompt = PromptTemplate(
                template=load_prompt("resolve_analysis_task"),
                input_variables=[
                    "original_question",
                    "task_question",
                    "task_purpose",
                    "dependency_results",
                ],
                partial_variables={
                    "format_instructions": parser.get_format_instructions()
                },
            )
            resolved_raw = await _stream_structured_llm(
                prompt=prompt,
                input_values={
                    "original_question": state.get("input_text", ""),
                    "task_question": task["question"],
                    "task_purpose": task["purpose"],
                    "dependency_results": json.dumps(
                        [
                            {
                                "task_id": result["task_id"],
                                "calculation_result": result["calculation_result"],
                            }
                            for result in dependency_results
                        ],
                        ensure_ascii=False,
                        default=str,
                    ),
                },
                runtime=runtime,
                writer=writer,
                step=step,
                task_id=task["task_id"],
                phase=current_phase,
            )
            resolved = _parse_structured_output(parser, resolved_raw)
            resolved_question = resolved.question
            writer(
                {
                    "type": "analysis_task_resolved",
                    "step": step,
                    "node": "execute_analysis",
                    "status": "success",
                    "task_id": task["task_id"],
                    "question": resolved_question,
                }
            )

        # Query Agent 负责独立召回当前任务所需的维度、指标和关联关系。
        # 同时消费 custom/value 两类事件，既转发过程，也拿到最终 sql_result。
        current_phase = "查询数据"
        query_state: AgentState = {
            "input_text": resolved_question,
            "original_question": resolved_question,
            "user_id": state.get("user_id", ""),
            "conversation_id": state.get("conversation_id", ""),
            "thread_id": state.get("thread_id", ""),
            "turn_id": state.get("turn_id", ""),
            "run_id": state.get("run_id", ""),
            "asset_ids": list(state.get("asset_ids", [])),
            "query_max_rows": state.get("query_max_rows", 2_000),
        }
        async for query_event in query_graph.astream(
            query_state,
            context=runtime.context,
            stream_mode=["custom", "values"],
        ):
            if isinstance(query_event, tuple):
                mode, payload = query_event
                if mode == "custom":
                    writer({**payload, "task_id": task["task_id"]})
                elif mode == "values":
                    query_result = payload
            elif isinstance(query_event, dict) and "sql_result" in query_event:
                query_result = query_event

        # SQL 执行完成后，画像和 Python 生成都基于本次查询的真实结果。
        rows = query_result.get("sql_result", [])
        if not rows:
            raise ValueError("Query Agent 没有返回可用于计算的数据")

        data_profile = _build_data_profile(rows)
        columns = data_profile["columns"]
        current_phase = "生成 Python 分析代码"
        writer(
            {
                    "type": "analysis_task_phase",
                    "step": step,
                    "node": "execute_analysis",
                "status": "running",
                "task_id": task["task_id"],
                "phase": "生成 Python 分析代码",
                "message": "查询完成，正在等待 LLM 生成 Python 分析代码。",
            }
        )
        parser = PydanticOutputParser(pydantic_object=PythonCalculation)
        prompt = PromptTemplate(
            template=load_prompt("generate_analysis_python"),
            input_variables=[
                "original_question",
                "task_question",
                "task_purpose",
                "data_profile",
            ],
            partial_variables={
                "format_instructions": parser.get_format_instructions()
            },
        )
        calculation_raw = await _stream_structured_llm(
            prompt=prompt,
            input_values={
                "original_question": state.get("input_text", ""),
                "task_question": resolved_question,
                "task_purpose": task["purpose"],
                "data_profile": json.dumps(
                    data_profile, ensure_ascii=False, default=str
                ),
            },
            runtime=runtime,
            writer=writer,
            step=step,
            task_id=task["task_id"],
            phase=current_phase,
        )
        calculation = _parse_structured_output(parser, calculation_raw)
        # LLM 只生成计算程序；数值比较、排名和差值由沙箱中的 Python 完成。
        calculation_code = calculation.code
        calculation_description = calculation.result_description
        current_phase = "执行 Python 计算"
        writer(
            {
                    "type": "analysis_task_phase",
                    "step": step,
                    "node": "execute_analysis",
                "status": "running",
                "task_id": task["task_id"],
                "phase": "执行 Python 计算",
                "message": "Python 分析代码已生成，正在执行计算。",
            }
        )
        calculation_result = _normalize_calculation_result(
            await execute_python_calculation(calculation_code, rows)
        )
        if isinstance(calculation_result, dict) and calculation_result.get("error"):
            raise ValueError(f"Python 计算未得到有效结果：{calculation_result['error']}")
        _validate_decrease_result(calculation_result, task)
        task_result = TaskResult(
            task_id=task["task_id"],
            status="success",
            question=task["question"],
            resolved_question=resolved_question,
            purpose=task["purpose"],
            depends_on=task["depends_on"],
            sql=query_result.get("sql", ""),
            rows=rows,
            columns=columns,
            data_profile=data_profile,
            python_code=calculation_code,
            calculation_description=calculation_description,
            calculation_result=calculation_result,
            result_columns=query_result.get("result_columns", []),
            dimension_value_mappings=query_result.get(
                "dimension_value_mappings", []
            ),
            display_sql_result=query_result.get("display_sql_result", []),
            mapping_limitations=query_result.get("mapping_limitations", []),
        )
    except Exception as exc:
        if isinstance(exc, TimeoutError) and is_llm_timeout(exc):
            call_mode, timeout_kind = classify_timeout(exc)
            record_llm_timeout(
                node="execute_analysis",
                step=step,
                task_id=task["task_id"],
                phase=current_phase,
                call_mode=call_mode,
                timeout_kind=timeout_kind,
                timeout_seconds=runtime.context.get("llm_timeout_seconds", 0),
                error=exc,
                llm_client=runtime.context.get("llm_client"),
                writer=writer,
            )
        logger.exception("分析任务执行失败：task_id=%s", task["task_id"])
        task_result = TaskResult(
            task_id=task["task_id"],
            status="failed",
            question=task["question"],
            resolved_question=resolved_question,
            purpose=task["purpose"],
            depends_on=task["depends_on"],
            sql=query_result.get("sql", ""),
            rows=rows,
            columns=columns,
            data_profile=data_profile,
            python_code=calculation_code,
            calculation_description=calculation_description,
            result_columns=query_result.get("result_columns", []),
            dimension_value_mappings=query_result.get(
                "dimension_value_mappings", []
            ),
            display_sql_result=query_result.get("display_sql_result", []),
            mapping_limitations=query_result.get("mapping_limitations", []),
            error=str(exc),
        )

    result = task_result.model_dump()
    writer({"type": "analysis_task_result", "step": step, "node": "execute_analysis", "task_id": task["task_id"], **result})
    return result


async def execute_analysis(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> dict[str, Any]:
    """按依赖层执行任务，同层任务并行运行。

    每轮先找出所有依赖已完成的任务：第一层通常是根查询，后续层
    使用前置计算结果。同一层任务互不依赖时并行执行，例如商品类别
    与卖家地区两个下钻任务。
    """
    plan = state.get("analysis_plan", {})
    pending_tasks = list(plan.get("tasks", []))
    if not pending_tasks:
        raise ValueError("分析计划中没有可执行任务")

    # 用 task_id 保存结果，既用于依赖解析，也用于最终按计划顺序输出。
    completed_results = {}
    while pending_tasks:
        # 当前轮，只执行依赖已经满足的任务，避免下游提前查询。
        ready_tasks = [
            task
            for task in pending_tasks
            if all(task_id in completed_results for task_id in task["depends_on"])
        ]
        if not ready_tasks:
            raise ValueError("分析计划中的任务依赖无法继续执行")

        # 同层任务共享只读的前置结果，因此可以并行执行。
        layer_results = await asyncio.gather(
            *(
                _execute_task(task, state, completed_results, runtime)
                for task in ready_tasks
            )
        )
        completed_results.update(
            {result["task_id"]: result for result in layer_results}
        )
        ready_task_ids = {task["task_id"] for task in ready_tasks}
        pending_tasks = [
            task for task in pending_tasks if task["task_id"] not in ready_task_ids
        ]

    task_results = [
        completed_results[task["task_id"]] for task in plan["tasks"]
    ]
    successful_task_ids = [
        result["task_id"]
        for result in task_results
        if result["status"] == "success"
    ]
    failed_task_ids = [
        result["task_id"]
        for result in task_results
        if result["status"] == "failed"
    ]
    if not failed_task_ids:
        evidence_status = "success"
    elif successful_task_ids:
        evidence_status = "partial"
    else:
        evidence_status = "failed"

    # 汇总阶段只建立证据边界，后续 Insight Synthesizer 再消费这份结构。
    evidence = AnalysisEvidence(
        original_question=state.get("input_text", ""),
        analysis_summary=plan.get("analysis_summary", ""),
        status=evidence_status,
        task_results=[
            AnalysisEvidenceTask(
                task_id=result["task_id"],
                status=result["status"],
                purpose=result["purpose"],
                depends_on=result["depends_on"],
                resolved_question=result["resolved_question"],
                sql=result["sql"],
                row_count=len(result["rows"]),
                columns=result["columns"],
                calculation_description=result["calculation_description"],
                calculation_result=result["calculation_result"],
                result_columns=result["result_columns"],
                dimension_value_mappings=result["dimension_value_mappings"],
                mapping_limitations=result["mapping_limitations"],
                error=result["error"],
            )
            for result in task_results
        ],
        successful_task_ids=successful_task_ids,
        failed_task_ids=failed_task_ids,
    )
    evidence_result = evidence.model_dump()
    runtime.stream_writer(
        {
            "type": "analysis_evidence",
            "step": "汇总全部分析证据",
            "node": "execute_analysis",
            "status": evidence_status,
            "analysis_evidence": evidence_result,
        }
    )

    logger.info("分析任务执行完成：%s", task_results)
    return {
        "analysis_task_results": task_results,
        "analysis_evidence": evidence_result,
        "output_text": json.dumps(evidence_result, ensure_ascii=False, default=str),
    }
