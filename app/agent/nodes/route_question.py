"""问题路由节点。

路由只决定后续执行模式，不负责查询、分析或回答业务问题：

daily_chat
    -> 进入普通聊天节点
single_query
    -> 进入现有 Query Agent
analysis
    -> 进入 Analysis Planner 和任务执行器
clarification
    -> 在路由边界返回澄清问题
"""

import json
import logging
from typing import Any, Literal

from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import PromptTemplate
from langgraph.runtime import Runtime
from pydantic import BaseModel, Field

from app.agent.context import AgentContext
from app.agent.prompts.prompt_loader import load_prompt
from app.agent.state import AgentState

logger = logging.getLogger(__name__)


class RouteDecision(BaseModel):
    """LLM 对用户问题给出的执行路径判断。

    该模型既校验 LLM 输出，也通过 PydanticOutputParser 将字段说明写入
    路由提示词的输出格式约束。
    """

    # 决定 LangGraph 条件边的目标节点。
    execution_mode: Literal[
        "daily_chat", "single_query", "analysis", "clarification"
    ] = Field(
        default="single_query",
        description=(
            "执行模式：daily_chat 表示日常聊天；single_query 表示单次查询即可回答；"
            "analysis 表示需要多步查询、计算或解释；clarification 表示缺少关键信息，需要先向用户澄清。"
        ),
    )
    # 用于日志和 SSE，帮助定位模型为什么选择当前路径。
    reason: str = Field(
        default="",
        description="选择当前执行模式的简明理由，不回答用户的业务问题。",
    )
    # 只在 analysis 模式下使用，交给 Planner 生成查询任务。
    analysis_goals: list[str] = Field(
        default_factory=list,
        description=(
            "analysis 模式下需要完成的分析目标列表；其他模式返回空列表。"
            "目标描述要说明需要比较、计算或解释什么，不生成具体 SQL。"
        ),
    )
    # 保留模型对路由判断的置信度，当前不直接用它阻断流程。
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="对执行模式判断的置信度，取值范围为 0 到 1。",
    )
    # clarification 模式下的用户可见问题。
    clarification_question: str = Field(
        default="",
        description=(
            "clarification 模式下向用户提出的最小澄清问题；其他模式返回空字符串。"
        ),
    )


async def route_question(
    state: AgentState,
    runtime: Runtime[AgentContext],
) -> dict[str, Any]:
    """在进入查询链前确定最小执行模式。"""
    writer = runtime.stream_writer
    step = "判断问题路由"
    writer({"type": "progress", "step": step, "node": "route_question", "status": "running"})

    question = state.get("input_text", "").strip()
    # 空输入不调用 LLM，直接返回确定性的澄清问题。
    if not question:
        decision = RouteDecision(
            execution_mode="clarification",
            reason="用户问题为空。",
            confidence=1.0,
            clarification_question="请告诉我你想查询或分析的业务问题。",
        )
    else:
        parser = PydanticOutputParser(pydantic_object=RouteDecision)
        prompt = PromptTemplate(
            template=load_prompt("route_question"),
            input_variables=["query"],
            partial_variables={"format_instructions": parser.get_format_instructions()},
        )
        # LLM 输出通过 Pydantic 校验；解析失败时回退到单次查询，
        # 保证路由节点不会因为格式问题阻断已有问数链。
        try:
            decision = await (prompt | runtime.context["llm_client"] | parser).ainvoke(
                {"query": question}
            )
        except Exception as exc:
            logger.warning("问题路由失败，回退到单次查询：%s", exc)
            decision = RouteDecision(
                reason="路由模型输出无效，回退到单次查询。",
                confidence=0.0,
            )

    # 清理重复和空白目标，避免 Planner 收到无效任务要求。
    goals = list(
        dict.fromkeys(goal.strip() for goal in decision.analysis_goals if goal.strip())
    )
    mode = decision.execution_mode
    clarification_question = decision.clarification_question.strip()
    if mode == "clarification" and not clarification_question:
        clarification_question = (
            decision.reason.strip() or "请补充需要分析的指标、范围或时间。"
        )
    # 统一规范澄清问题后再写回模型，保持事件、State 和返回值一致。
    decision = decision.model_copy(
        update={
            "execution_mode": mode,
            "analysis_goals": goals,
            "clarification_question": clarification_question,
        }
    )
    # 把 Pydantic 模型转换成普通 Python 字典，便于写入 State 和序列化。
    result = decision.model_dump()
    logger.info(
        "问题路由判断：问题=%r，执行模式=%s，原因=%s，分析目标=%s，澄清问题=%s，置信度=%.2f",
        question,
        result["execution_mode"],
        result["reason"],
        result["analysis_goals"],
        result["clarification_question"],
        result["confidence"],
    )
    writer(
        {
            "type": "question_route",
            "step": step,
            "node": "route_question",
            "status": "success",
            **result,
        }
    )
    return {
        "execution_mode": result["execution_mode"],
        "route_reason": result["reason"],
        "analysis_goals": result["analysis_goals"],
        "route_confidence": result["confidence"],
        "clarification_question": result["clarification_question"],
        "route_output": json.dumps(result, ensure_ascii=False),
    }
