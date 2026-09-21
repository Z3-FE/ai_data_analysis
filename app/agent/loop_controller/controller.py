"""Data Agent Harness 的统一执行循环。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.agent.context_engine.harness_context import HarnessContextRequestFactory
from app.agent.context_engine.contracts import CompiledContext, ContextSourceKind
from app.agent.finalization.errors import FinalizationFailure
from app.agent.loop_controller.action_commit import ActionCommitRequest, ActionCommitter
from app.agent.loop_controller.contracts import (
    ConfirmationDispatcher,
    ContextBuilder,
    FinalizationInput,
    FinalizationPort,
    FinalizationResult,
    HarnessRunStore,
    LoopPausedResult,
    LoopResult,
    LoopResumeAcceptedResult,
    LoopRunResult,
    PlanningPort,
    ResumeRunCommand,
    StartRunCommand,
    ToolRuntimePort,
)
from app.agent.planning_agent.contracts import ActionIssuanceContext
from app.agent.planning_agent.errors import PlannerFailure
from app.agent.state import HarnessGraphState
from app.agent.state_result_store.contracts import (
    ActionType,
    AskUserRequest,
    ConfirmationRecord,
    ConfirmationRequest,
    ConfirmationStatus,
    ConfirmationVisibility,
    ErrorCategory,
    HarnessStatusType,
    HarnessStateSnapshot,
    LoopPhaseStatusType,
    PlannerInput,
    PlannerStateView,
    ResultStatus,
    RunError,
    RunExecutionFence,
    RunObservation,
    ToolSpec,
)
from app.agent.state_result_store.state import (
    new_harness_control_state,
    transition_harness_state,
)
from app.agent.streaming.contracts import EventType
from app.agent.streaming.writer import HarnessEventWriter
from app.agent.tool_runtime.contracts import ToolExecutionRequest

logger = logging.getLogger(__name__)


class HarnessDeadlineExceeded(TimeoutError):
    """当前 Harness 运行超过了持久化的 deadline_at。"""


class LoopController:
    """Harness 唯一主循环；每个工具结果都会进入下一次 Context 构建。"""

    # 同一确认请求重复出现表示 Planner 没有消费用户输入，继续等待没有意义。
    _max_confirmation_attempts = 3

    def __init__(
        self,
        *,
        context_builder: ContextBuilder,
        planning_agent: PlanningPort,
        finalization_service: FinalizationPort,
        run_store: HarnessRunStore, # HarnessRunStore的操作
        context_request_factory: HarnessContextRequestFactory | None = None,
        system_instructions: str = "你是一个数据分析助手。",
        action_committer: ActionCommitter | None = None,
        tool_runtime: ToolRuntimePort | None = None,
        confirmation_dispatcher: ConfirmationDispatcher | None = None,
        tool_specs: tuple[ToolSpec, ...] = (),
        max_planner_retries: int = 2,
        max_tool_retries: int = 2,
        max_iterations: int = 8,
        run_timeout_seconds: int = 300,
        event_writer: HarnessEventWriter,
    ) -> None:
        if max_planner_retries < 0:
            raise ValueError("max_planner_retries 不能小于 0")
        if max_tool_retries < 0:
            raise ValueError("max_tool_retries 不能小于 0")
        if max_iterations <= 0:
            raise ValueError("max_iterations 必须大于 0")
        if run_timeout_seconds <= 0:
            raise ValueError("run_timeout_seconds 必须大于 0")
        self.context_builder = context_builder
        self.planning_agent = planning_agent
        self.finalization_service = finalization_service
        self.run_store = run_store
        self.context_request_factory = (
            context_request_factory or HarnessContextRequestFactory()
        )
        self.system_instructions = system_instructions
        self.action_committer = action_committer
        self.tool_runtime = tool_runtime
        self.confirmation_dispatcher = confirmation_dispatcher
        self.tool_specs = tool_specs
        self.max_planner_retries = max_planner_retries
        self.max_tool_retries = max_tool_retries
        self.max_iterations = max_iterations
        self.run_timeout_seconds = run_timeout_seconds
        self.event_writer = event_writer

    async def start(self, command: StartRunCommand) -> LoopResult:
        """创建新运行并执行，直到暂停或进入终态。"""
        # 1.新建状态： state
        # 状态类型：status=HarnessStatusType.RUNNING, phase=LoopPhaseStatusType.START_RUN
        state: HarnessGraphState = self._new_state(command)

        await self.run_store.create(command.run_ref, state) #把run_store， 写入 PG数据库
        self.event_writer.emit(
            EventType.RUN_STARTED,
            phase=LoopPhaseStatusType.START_RUN,
            iteration=0,
        )
        logger.info(
            "Harness status=%s: run_id=%s turn_id=%s",
            EventType.RUN_STARTED,
            command.run_ref.run_id,
            command.run_ref.turn_id,
            extra={"run_id": command.run_ref.run_id, "turn_id": command.run_ref.turn_id},
        )
        return await self._run_guarded(command, state)

    async def resume(self, command: ResumeRunCommand) -> LoopResult:
        """原子消费用户确认，并在同一 run、turn 和 thread 上恢复。"""
        resolution = await self.run_store.resolve_confirmation(
            command.run_ref, command.reply
        )
        state = resolution.state
        harness = state["harness"]
        if resolution.status == "idempotent":
            self.event_writer.emit(
                EventType.CONFIRMATION_RESOLVED,
                phase=LoopPhaseStatusType.RESTORE_RUN,
                iteration=int(harness["iteration"]),
                payload={"status": "idempotent"},
            )
            return LoopResumeAcceptedResult(
                run_ref=command.run_ref,
                resume_status="idempotent",
                status=HarnessStatusType(harness["status"]),
                phase=LoopPhaseStatusType(harness["phase"]),
                state_version=int(harness["state_version"]),
            )
        self.event_writer.emit(
            EventType.CONFIRMATION_RESOLVED,
            phase=LoopPhaseStatusType.RESTORE_RUN,
            iteration=int(harness["iteration"]),
            payload={"status": resolution.status},
        )

        restored_command = StartRunCommand(
            run_ref=command.run_ref,
            input_text=state["input_text"],
            project_id=state.get("project_id"),
            asset_ids=tuple(state.get("asset_ids", ())),
        )
        if resolution.status == "rejected":
            return await self._finalize(
                command=restored_command,
                state=state,
                compiled_context=None,
                final_answer="用户拒绝了本次确认，任务已停止。",
                terminal_status=HarnessStatusType.CANCELLED,
            )
        return await self._run_guarded(restored_command, state)

    async def _run_guarded(
        self,
        command: StartRunCommand,
        state: HarnessGraphState,
    ) -> LoopResult:
        """把运行级超时和 HTTP 请求取消收口为可查询的 Harness 终态。"""
        try:
            return await self._run(command, state)
        except HarnessDeadlineExceeded:
            state = await self._prepare_interruption_state(
                command=command,
                state=state,
                code="run_deadline_exceeded",
                message="Harness 运行超过 deadline_at，已停止继续调用外部依赖。",
            )
            return await self._finalize(
                command=command,
                state=state,
                compiled_context=None,
                final_answer="任务执行超过时间限制，已停止继续处理。",
                terminal_status=HarnessStatusType.TIMEOUT,
            )
        except asyncio.CancelledError:
            # 客户端断开时仍完成一次终态落库；shield 防止请求取消再次取消收口任务。
            state = await self._prepare_interruption_state(
                command=command,
                state=state,
                code="run_cancelled",
                message="Harness 运行被请求取消，已停止继续处理。",
            )
            finalization_task = asyncio.create_task(
                self._finalize(
                    command=command,
                    state=state,
                    compiled_context=None,
                    final_answer="任务已取消，未继续执行后续分析。",
                    terminal_status=HarnessStatusType.CANCELLED,
                )
            )
            try:
                return await asyncio.shield(finalization_task)
            except asyncio.CancelledError:
                # 当前请求已无法返回结果，让收口任务继续完成并消费异常。
                finalization_task.add_done_callback(self._consume_task_result)
                raise
        except FinalizationFailure:
            # 收口中断必须保持 running/finalization 现场，交给 reconcile 恢复；
            # 在这里降级成 FAILED 会伪造一个用户从未见过的终态。
            raise
        except Exception as exc:
            # 未预期异常也必须释放 active_run，不能让 PostgreSQL 永久停留在 running。
            state = await self._prepare_interruption_state(
                command=command,
                state=state,
                code="harness_execution_failed",
                message=str(exc)[:2_000] or "Harness 执行失败。",
            )
            return await self._finalize(
                command=command,
                state=state,
                compiled_context=None,
                final_answer="任务执行失败，已停止后续处理。",
                terminal_status=HarnessStatusType.FAILED,
            )

    @staticmethod
    def _consume_task_result(task: asyncio.Task) -> None:
        """消费断开请求留下的后台收口任务异常，避免未取结果警告。"""
        try:
            task.result()
        except BaseException:
            pass

    @staticmethod
    def _set_interruption_error(
        state: HarnessGraphState,
        *,
        code: str,
        message: str,
    ) -> None:
        """把运行级中断写进下一次可恢复/可查询的 Harness 现场。"""
        state["harness"]["last_error"] = RunError(
            category=ErrorCategory.TIMEOUT
            if code == "run_deadline_exceeded"
            else ErrorCategory.CANCELLED,
            code=code,
            message=message,
            retryable=False,
        ).model_dump(mode="json")

    @staticmethod
    def _remaining_seconds(state: HarnessGraphState) -> float | None:
        """读取持久化 deadline_at，返回当前运行剩余秒数。"""
        snapshot = HarnessStateSnapshot.model_validate(state["harness"])
        if snapshot.deadline_at is None:
            return None
        return (snapshot.deadline_at - datetime.now(UTC)).total_seconds()

    def _check_deadline(self, state: HarnessGraphState) -> None:
        """在每个循环边界阻止已过期运行继续调用 Planner 或工具。"""
        remaining = self._remaining_seconds(state)
        if remaining is not None and remaining <= 0:
            raise HarnessDeadlineExceeded("Harness 运行已超过 deadline_at")

    async def _await_with_deadline(self, awaitable, state: HarnessGraphState):
        """让单个外部调用服从运行级 deadline，而不是只依赖组件超时。"""
        remaining = self._remaining_seconds(state)
        if remaining is None:
            return await awaitable
        if remaining <= 0:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise HarnessDeadlineExceeded("Harness 运行已超过 deadline_at")
        try:
            return await asyncio.wait_for(awaitable, timeout=remaining)
        except asyncio.TimeoutError as exc:
            # 只有确实触及运行 deadline 才转换；组件自己的 TimeoutError 保持原语义。
            if self._remaining_seconds(state) is not None and self._remaining_seconds(state) <= 0:
                raise HarnessDeadlineExceeded("Harness 运行已超过 deadline_at") from exc
            raise

    async def _prepare_interruption_state(
        self,
        *,
        command: StartRunCommand,
        state: HarnessGraphState,
        code: str,
        message: str,
    ) -> HarnessGraphState:
        """以最后一个已持久化版本为基础准备 timeout/cancelled 收口。"""
        try:
            persisted_state = await self.run_store.load(command.run_ref)
        except Exception:
            # 持久化读取失败时保留内存现场，让 Finalization 的保存操作报告真实冲突。
            persisted_state = state
        state = persisted_state
        if state["harness"]["status"] == HarnessStatusType.WAITING_CONFIRMATION.value:
            state = self._transition(
                state,
                status=HarnessStatusType.RUNNING,
                phase=LoopPhaseStatusType.RESTORE_RUN,
            )
        self._set_interruption_error(state, code=code, message=message)
        return state

    async def _run(
        self,
        command: StartRunCommand,
        state: HarnessGraphState,
    ) -> LoopResult:
        """从 start_run 或 restore_run 进入统一上下文、规划和工具循环。"""

        # ① 入口：deadline 预检 → 迁移 BUILD_CONTEXT → 落库。
        #    后文固定节奏都是「_transition 推进阶段指针 → _save_running_state 落库」，
        #    保证现场每一步都可恢复。
        self._check_deadline(state) # 检查是否超时
        state = self._transition(
            state, status=HarnessStatusType.RUNNING, phase=LoopPhaseStatusType.BUILD_CONTEXT
        )
        await self._save_running_state(command, state)
        # 从现场读 phase 而不是写字面量：迁移目标改了，日志自动跟着变，不会两处漂移
        logger.info(
            "Harness phase=%s: run_id=%s turn_id=%s",
            state["harness"]["phase"],
            command.run_ref.run_id,
            command.run_ref.turn_id,
            extra={"run_id": command.run_ref.run_id, "turn_id": command.run_ref.turn_id},
        )

        # ② 构建首轮上下文（记忆读取 + 元数据召回），构建期间同样受 deadline 约束
        compiled_context = await self._await_with_deadline(
            self._build_context(state), state
        )
        # ③ 迁移 PLAN 并落库，组装 Planner 输入，进入"规划→执行"主循环
        state = self._transition(
            state, status=HarnessStatusType.RUNNING, phase=LoopPhaseStatusType.PLAN
        )
        await self._save_running_state(command, state)
        planner_input = self._planner_input(state, compiled_context)

        # 主循环：每轮 = 规划一个动作 → 提交前拦截 → 提交落库 → 按类型分派 → 记录观察 → 重建上下文
        while True:
            self._check_deadline(state)
            # ④ 规划：调 Planner 产出下一个动作；PlannerFailure 可重试则重算输入重来，
            #    重试额度耗尽则按 FAILED 收口
            try:
                action = await self._await_with_deadline(
                    self._plan_action(command, state, planner_input), state
                )
            except PlannerFailure as failure:
                if await self._handle_planner_failure(
                    command=command, state=state, failure=failure
                ):
                    planner_input = self._planner_input(state, compiled_context)
                    continue
                return await self._finalize(
                    command=command,
                    state=state,
                    compiled_context=compiled_context,
                    final_answer="任务规划多次失败，暂时无法完成本次分析。",
                    terminal_status=HarnessStatusType.FAILED,
                )

            # ⑤ 拿到合法动作：清重试计数与 planner 类 last_error
            # 重试额度属于当前规划阶段；成功发行动作后，下一次规划重新计数。
            state["harness"]["planner_retry_count"] = 0
            last_error = state["harness"].get("last_error")
            if isinstance(last_error, dict) and last_error.get("category") == "planner":
                state["harness"]["last_error"] = None
            state = self._transition(
                state, status=HarnessStatusType.RUNNING, phase=LoopPhaseStatusType.VALIDATE_ACTION
            )
            await self._save_running_state(command, state)
            # ⑥ 退化路径：没有动作提交器时只允许直接作答；工具/确认没有审计与幂等保障，直接拒绝
            if self.action_committer is None:
                if action.action_type is ActionType.FINAL_ANSWER:
                    return await self._finalize(
                        command=command,
                        state=state,
                        compiled_context=compiled_context,
                        final_answer=action.final_answer,
                    )
                raise ValueError("执行工具或用户确认必须注入 ActionCommitter")

            # ⑦ 提交前拦截：失败请求的重复工具调用、重复确认都在动作落库前终止，
            #    避免留下"已 committed 但不会执行"的动作
            request_hash = None
            if action.action_type is ActionType.TOOL_CALL:
                if action.tool_call is None:
                    raise ValueError("TOOL_CALL 动作必须包含 tool_call")
                if self.tool_runtime is None:
                    raise ValueError("TOOL_CALL 必须注入 ToolRuntime")
                # 失败请求必须在提交动作前拦截，避免留下不会执行的 committed 动作。
                request_hash = self._tool_request_hash(
                    action.tool_call.tool_name, action.tool_call.arguments
                )
                if self._has_failed_tool_request(state, request_hash):
                    return await self._finalize(
                        command=command,
                        state=state,
                        compiled_context=compiled_context,
                        final_answer="相同的数据工具请求已经失败，已停止重复执行。",
                        terminal_status=HarnessStatusType.FAILED,
                    )

            # 重复确认在动作提交前终止，避免产生没有对应 pending 记录的 committed 动作。
            if action.action_type is ActionType.ASK_USER and self.confirmation_dispatcher is None:
                assert action.ask_user is not None
                if self._should_stop_for_repeated_confirmation(
                    state, action.ask_user
                ):
                    return await self._finalize(
                        command=command,
                        state=state,
                        compiled_context=compiled_context,
                        final_answer="确认回复未能解决当前问题，任务已停止。",
                        terminal_status=HarnessStatusType.FAILED,
                    )
            # ⑧ 提交动作落库：commit 先于执行，动作序列号是审计、幂等与恢复的依据
            await self._await_with_deadline(
                self._commit_action(command, state, action), state
            )
            # ⑨ 按动作类型分派：作答收口 / 要用户确认 / 执行工具
            if action.action_type is ActionType.FINAL_ANSWER:
                return await self._finalize(
                    command=command,
                    state=state,
                    compiled_context=compiled_context,
                    final_answer=action.final_answer,
                )
            if action.action_type is ActionType.ASK_USER:
                if self.confirmation_dispatcher is not None:
                    return await self._finalize(
                        command=command,
                        state=state,
                        compiled_context=compiled_context,
                        final_answer=await self.confirmation_dispatcher.dispatch(action),
                    )
                assert action.ask_user is not None
                return await self._await_with_deadline(
                    self._pause_for_confirmation(
                        command=command,
                        state=state,
                        request=action.ask_user,
                    ),
                    state,
                )
            if action.action_type is not ActionType.TOOL_CALL or action.tool_call is None:
                raise ValueError("未知动作类型，禁止进入执行阶段")
            if self.tool_runtime is None:
                raise ValueError("TOOL_CALL 必须注入 ToolRuntime")

            # ⑩ 执行工具：迁移 EXECUTE_TOOL 并落库后，真正调用工具运行时（受 deadline 约束）
            state = self._transition(
                state, status=HarnessStatusType.RUNNING, phase=LoopPhaseStatusType.EXECUTE_TOOL
            )
            await self._save_running_state(command, state)
            tool_result = await self._await_with_deadline(
                self._execute_tool(
                    command=command,
                    state=state,
                    action=action,
                ),
                state,
            )
            # ⑪ 处理结果：构造受控观察入档（Planner 下一轮只见观察摘要与引用，见不到完整结果），
            #    并把工具错误写入 last_error
            state = self._transition(
                state, status=HarnessStatusType.RUNNING, phase=LoopPhaseStatusType.HANDLE_TOOL_RESULT
            )
            await self._save_running_state(command, state)
            observation = RunObservation(
                observation_id=(
                    f"{command.run_ref.run_id}:o"
                    f"{len(state['harness']['observations']) + 1}"
                ),
                action_id=action.tool_call.action_id,
                tool_name=tool_result.tool_name,
                status=tool_result.status,
                summary=tool_result.summary,
                result_ref=tool_result.result_ref,
                evidence_refs=tool_result.evidence_refs,
                limitations=tool_result.limitations,
                output_hash=tool_result.output_hash,
                request_hash=request_hash,
            )
            state["harness"]["observations"].append(observation.model_dump(mode="json"))
            error = self._tool_error(tool_result)
            state["harness"]["last_error"] = (
                error.model_dump(mode="json") if error is not None else None
            )
            # ⑫ 工具结果要求人工确认（needs_user）：同样先拦重复确认，再暂停等待
            if tool_result.status is ResultStatus.NEEDS_USER:
                assert tool_result.confirmation_request is not None
                if self._should_stop_for_repeated_confirmation(
                    state, tool_result.confirmation_request
                ):
                    return await self._finalize(
                        command=command,
                        state=state,
                        compiled_context=compiled_context,
                        final_answer="确认回复未能解决当前问题，任务已停止。",
                        terminal_status=HarnessStatusType.FAILED,
                    )
                return await self._await_with_deadline(
                    self._pause_for_confirmation(
                        command=command,
                        state=state,
                        request=tool_result.confirmation_request,
                    ),
                    state,
                )
            # ⑬ 记录观察：推进迭代计数并落库；达到上限则按 TIMEOUT 收口，不再继续规划
            state = self._transition(
                state, status=HarnessStatusType.RUNNING, phase=LoopPhaseStatusType.RECORD_OBSERVATION
            )
            state["harness"]["iteration"] += 1
            await self._save_running_state(command, state)
            if state["harness"]["iteration"] >= state["harness"]["max_iterations"]:
                return await self._finalize(
                    command=command,
                    state=state,
                    compiled_context=compiled_context,
                    final_answer="任务达到最大工具迭代次数，无法继续安全执行。",
                    terminal_status=HarnessStatusType.TIMEOUT,
                )

            # ⑭ 下一轮：带着刚记录的观察重建上下文 → 回到 PLAN，循环继续
            state = self._transition(
                state, status=HarnessStatusType.RUNNING, phase=LoopPhaseStatusType.BUILD_CONTEXT
            )
            await self._save_running_state(command, state)
            compiled_context = await self._await_with_deadline(
                self._build_context(state), state
            )
            state = self._transition(
                state, status=HarnessStatusType.RUNNING, phase=LoopPhaseStatusType.PLAN
            )
            await self._save_running_state(command, state)
            planner_input = self._planner_input(state, compiled_context)

    async def _pause_for_confirmation(
        self,
        *,
        command: StartRunCommand,
        state: HarnessGraphState,
        request: AskUserRequest,
    ) -> LoopPausedResult:
        """创建确认凭证，并将请求和等待现场写入同一个 PostgreSQL 事务。"""
        now = datetime.now(UTC)
        confirmation = ConfirmationRequest(
            confirmation_id=(
                f"{command.run_ref.run_id}:confirmation:"
                f"{state['harness']['action_seq']}"
            ),
            question=request.question,
            reason_code=request.reason_code,
            required_fields=request.required_fields,
            expires_at=now + timedelta(hours=24),
        )
        digest = hashlib.sha256(
            json.dumps(
                confirmation.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        state["harness"]["pending_confirmation"] = confirmation.model_dump(
            mode="json"
        )
        state = self._transition(
            state,
            status=HarnessStatusType.WAITING_CONFIRMATION,
            phase=LoopPhaseStatusType.WAIT_CONFIRMATION,
        )
        await self.run_store.pause_for_confirmation(
            command.run_ref,
            state,
            ConfirmationRecord(
                run_ref=command.run_ref,
                request=confirmation,
                request_digest=digest,
                status=ConfirmationStatus.PENDING,
                visibility=ConfirmationVisibility.PUBLISHED,
                prepared_at=now,
                published_at=now,
            ),
        )
        self.event_writer.emit(
            EventType.CONFIRMATION_REQUIRED,
            phase=LoopPhaseStatusType.WAIT_CONFIRMATION,
            iteration=int(state["harness"]["iteration"]),
            payload={
                "confirmation_id": confirmation.confirmation_id,
                "question": confirmation.question,
                "reason_code": confirmation.reason_code,
                "required_fields": list(confirmation.required_fields),
                "expires_at": confirmation.expires_at,
            },
        )
        return LoopPausedResult(
            run_ref=command.run_ref,
            iteration=int(state["harness"]["iteration"]),
            confirmation=confirmation,
            state_version=int(state["harness"]["state_version"]),
        )

    async def _handle_planner_failure(
        self,
        *,
        command: StartRunCommand,
        state: HarnessGraphState,
        failure: PlannerFailure,
    ) -> bool:
        """记录 Planner 失败；返回 True 表示允许在同一上下文上重试。"""
        harness = state["harness"]
        retry_count = int(harness["planner_retry_count"])
        harness["last_error"] = failure.error.model_dump(mode="json")
        if failure.error.retryable and retry_count < self.max_planner_retries:
            harness["planner_retry_count"] = retry_count + 1
            state = self._transition(
                state, status=HarnessStatusType.RUNNING, phase=LoopPhaseStatusType.PLAN
            )
            await self._save_running_state(command, state)
            self.event_writer.emit(
                EventType.PLANNER_RETRYING,
                phase=LoopPhaseStatusType.PLAN,
                iteration=int(state["harness"]["iteration"]),
                payload={
                    "retry_count": int(harness["planner_retry_count"]),
                    "error_code": failure.error.code,
                },
            )
            return True

        return False

    @classmethod
    def _should_stop_for_repeated_confirmation(
        cls, state: HarnessGraphState, request: AskUserRequest
    ) -> bool:
        """阻止 Planner 在没有消费用户回复时无限重复相同确认。"""
        harness = state["harness"]
        attempt_count = int(harness.get("confirmation_attempt_count", 0))
        if attempt_count >= cls._max_confirmation_attempts:
            return True
        previous_reason = str(harness.get("last_confirmation_reason_code") or "")
        previous_question = str(harness.get("last_confirmation_question") or "")
        return (
            previous_reason == request.reason_code
            and previous_question.strip() == request.question.strip()
        )

    async def _execute_tool(self, *, command, state, action):
        """只重试明确可安全重放的临时错误，不重复执行未知副作用。"""
        if self.tool_runtime is None or action.tool_call is None:
            raise ValueError("工具执行依赖未配置")
        action_id = action.tool_call.action_id
        tool_spec = next(
            (spec for spec in self.tool_specs if spec.name == action.tool_call.tool_name),
            None,
        )
        while True:
            retry_count = state["harness"]["tool_retry_counts"].get(action_id, 0)
            result = await self.tool_runtime.execute(
                ToolExecutionRequest(
                    run_ref=command.run_ref,
                    tool_call=action.tool_call,
                    action_seq=action.action_seq,
                    iteration=int(state["harness"]["iteration"]),
                    attempt=retry_count + 1,
                )
            )
            if (
                result.status is not ResultStatus.TEMPORARY_ERROR
                or not result.retryable
                or tool_spec is None
                or tool_spec.idempotency != "idempotent"
                or (
                    result.error_code == "tool_timeout"
                    and not tool_spec.retry_on_timeout
                )
                or retry_count >= self.max_tool_retries
            ):
                return result

            state["harness"]["tool_retry_counts"][action_id] = retry_count + 1
            error = self._tool_error(result)
            state["harness"]["last_error"] = (
                error.model_dump(mode="json") if error is not None else None
            )
            state = self._transition(
                state, status=HarnessStatusType.RUNNING, phase=LoopPhaseStatusType.EXECUTE_TOOL
            )
            await self._save_running_state(command, state)
            self.event_writer.emit(
                EventType.TOOL_RETRYING,
                phase=LoopPhaseStatusType.EXECUTE_TOOL,
                iteration=int(state["harness"]["iteration"]),
                action_id=action_id,
                payload={
                    "tool_name": action.tool_call.tool_name,
                    "attempt": retry_count + 2,
                    "error_code": result.error_code,
                },
            )

    @staticmethod
    def _tool_request_hash(tool_name: str, arguments: dict) -> str:
        """按工具名和规范化参数生成运行内请求指纹。"""
        payload = json.dumps(
            {"tool_name": tool_name, "arguments": arguments},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _has_failed_tool_request(state: HarnessGraphState, request_hash: str) -> bool:
        """相同工具请求只要已有失败观察，就不再交给模型重复执行。"""
        return any(
            observation.get("request_hash") == request_hash
            and observation.get("status")
            in {"temporary_error", "unrecoverable_error"}
            for observation in state["harness"].get("observations", [])
        )

    @staticmethod
    def _tool_error(tool_result) -> RunError | None:
        if tool_result.error_category is None:
            return None
        return RunError(
            category=tool_result.error_category,
            code=tool_result.error_code,
            message=tool_result.error_message,
            retryable=tool_result.retryable,
            action_id=tool_result.tool_call_id,
        )

    async def _plan_action(
        self,
        command: StartRunCommand,
        state: HarnessGraphState,
        planner_input: PlannerInput,
    ):
        iteration = int(state["harness"]["iteration"])
        action_seq = int(state["harness"]["action_seq"]) + 1
        self.event_writer.emit(
            EventType.PLANNER_STARTED,
            phase=LoopPhaseStatusType.PLAN,
            iteration=iteration,
            payload={"action_seq": action_seq},
        )
        try:
            action = await self.planning_agent.plan(
                planner_input,
                issuance=ActionIssuanceContext(
                    run_id=command.run_ref.run_id,
                    iteration=iteration,
                    action_seq=action_seq,
                ),
            )
        except PlannerFailure as exc:
            logger.error(
                "Planner 失败：run_id=%s iteration=%s action_seq=%s code=%s message=%s",
                command.run_ref.run_id,
                iteration,
                action_seq,
                exc.error.code,
                exc.error.message,
            )
            self.event_writer.emit(
                EventType.PLANNER_FAILED,
                phase=LoopPhaseStatusType.PLAN,
                iteration=iteration,
                payload={
                    "action_seq": action_seq,
                    "error_code": exc.error.code,
                    "error_message": exc.error.message,
                    "retryable": exc.error.retryable,
                },
            )
            raise
        except Exception as exc:
            logger.exception(
                "Planner 异常：run_id=%s iteration=%s action_seq=%s",
                command.run_ref.run_id,
                iteration,
                action_seq,
            )
            self.event_writer.emit(
                EventType.PLANNER_FAILED,
                phase=LoopPhaseStatusType.PLAN,
                iteration=iteration,
                payload={
                    "action_seq": action_seq,
                    "error_code": "planner_unexpected_error",
                    "error_message": str(exc)[:500],
                },
            )
            raise
        self.event_writer.emit(
            EventType.PLANNER_COMPLETED,
            phase=LoopPhaseStatusType.PLAN,
            iteration=iteration,
            payload={
                "action_seq": action.action_seq,
                "action_type": action.action_type.value,
                "tool_name": (
                    action.tool_call.tool_name
                    if action.tool_call is not None
                    else None
                ),
            },
        )
        return action

    async def _commit_action(self, command: StartRunCommand, state, action) -> None:
        expected_action_seq = state["harness"]["action_seq"] + 1
        if action.action_seq != expected_action_seq:
            raise ValueError("Planner 返回的 action_seq 不符合服务端发行序号")
        commit = await self.action_committer.commit(
            ActionCommitRequest(
                run_ref=command.run_ref,
                action=action,
                expected_action_seq=expected_action_seq,
                expected_state_version=state["harness"]["state_version"],
                expected_checkpoint_revision=state["harness"]["checkpoint_revision"],
                execution_fence=RunExecutionFence(
                    owner_id="loop-controller",
                    fencing_token=max(1, state["harness"]["fencing_token"]),
                    lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
                ),
            )
        )
        if commit.status not in {"committed", "idempotent"}:
            raise ValueError("动作未提交，禁止进入下游")
        if commit.action_seq != action.action_seq:
            raise ValueError("提交结果的 action_seq 与动作不一致")
        state["harness"]["action_seq"] = action.action_seq
        self.event_writer.emit(
            EventType.ACTION_COMMITTED,
            phase=LoopPhaseStatusType.VALIDATE_ACTION,
            iteration=int(state["harness"]["iteration"]),
            action_id=(
                action.tool_call.action_id
                if action.tool_call is not None
                else None
            ),
            payload={
                "action_seq": action.action_seq,
                "action_type": action.action_type.value,
                "commit_status": commit.status,
            },
        )

    # 构建上下文
    async def _build_context(self, state):
        run_ref = self._run_ref_from_state(state)
        phase = LoopPhaseStatusType(state["harness"]["phase"])
        self.event_writer.emit(
            EventType.CONTEXT_STARTED,
            phase=phase,
            iteration=int(state["harness"]["iteration"]),
        )
        try:
            request = self.context_request_factory.create(
                state,
                system_instructions=self.system_instructions,
                agent_type="data_agent",
            )
            compiled_context = await self.context_builder.build(request)
        except Exception:
            # context 是唯一可能 started 后没有终态的阶段；补发失败事件保证生命周期配对。
            self.event_writer.emit(
                EventType.CONTEXT_FAILED,
                phase=phase,
                iteration=int(state["harness"]["iteration"]),
                payload={"error_code": "context_build_failed"},
            )
            raise
        state["harness"]["last_context_build_id"] = compiled_context.build_id
        state["harness"]["last_context_token_count"] = compiled_context.token_count
        selected_by_kind: dict[str, int] = {}
        for decision in compiled_context.trace.decisions:
            if decision.selected:
                key = decision.source_kind.value
                selected_by_kind[key] = selected_by_kind.get(key, 0) + 1
        memory_count = sum(
            selected_by_kind.get(kind.value, 0)
            for kind in (
                ContextSourceKind.SEMANTIC,
                ContextSourceKind.EPISODIC,
                ContextSourceKind.PERCEPTUAL,
            )
        )
        knowledge_count = selected_by_kind.get(ContextSourceKind.RAG.value, 0)
        self.event_writer.emit(
            EventType.CONTEXT_MEMORY_RETRIEVED,
            phase=phase,
            iteration=int(state["harness"]["iteration"]),
            payload={
                "selected_count": memory_count,
                "source_counts": {
                    kind: selected_by_kind.get(kind, 0)
                    for kind in (
                        ContextSourceKind.SEMANTIC.value,
                        ContextSourceKind.EPISODIC.value,
                        ContextSourceKind.PERCEPTUAL.value,
                    )
                },
            },
        )
        self.event_writer.emit(
            EventType.CONTEXT_KNOWLEDGE_RETRIEVED,
            phase=phase,
            iteration=int(state["harness"]["iteration"]),
            payload={
                "selected_count": knowledge_count,
                "evidence_count": len(compiled_context.sections.external_evidence),
            },
        )
        snapshot = self._context_snapshot(compiled_context)
        plan = compiled_context.trace.retrieval_plan
        self.event_writer.emit(
            EventType.CONTEXT_PLAN,
            phase=phase,
            iteration=int(state["harness"]["iteration"]),
            payload={
                "include_working": plan.include_working,
                "memory_types": [item.value for item in plan.memory_types],
                "use_rag": plan.use_rag,
                "reason": plan.reason,
                "candidate_count": compiled_context.trace.candidate_count,
                "selected_count": compiled_context.trace.selected_count,
                "token_count": compiled_context.token_count,
            },
        )
        self.event_writer.emit(
            EventType.CONTEXT_COMPILED,
            phase=phase,
            iteration=int(state["harness"]["iteration"]),
            payload=snapshot,
        )
        self.event_writer.emit(
            EventType.CONTEXT_COMPLETED,
            phase=phase,
            iteration=int(state["harness"]["iteration"]),
        )
        logger.info(
            "Harness context compiled: run_id=%s build_id=%s token_count=%s selected_count=%s snapshot=%s",
            run_ref.run_id,
            compiled_context.build_id,
            compiled_context.token_count,
            compiled_context.trace.selected_count,
            snapshot,
            extra={
                "run_id": run_ref.run_id,
                "context_build_id": compiled_context.build_id,
                "context_token_count": compiled_context.token_count,
                "context_selected_count": compiled_context.trace.selected_count,
                "context_snapshot": snapshot,
            },
        )
        return compiled_context

    @staticmethod
    def _context_snapshot(compiled_context: CompiledContext) -> dict[str, Any]:
        """生成可展示的上下文快照，不把内部对象直接暴露给事件层。"""
        sections = compiled_context.sections
        trace = compiled_context.trace
        return {
            "build_id": compiled_context.build_id,
            "token_count": compiled_context.token_count,
            "resolved_asset_ids": list(compiled_context.resolved_asset_ids),
            "model_messages": [
                {
                    "role": str(message.get("role", "")),
                    "text": str(message.get("content", "")),
                }
                for message in compiled_context.messages
            ],
            "sections": {
                "conversation_summary": list(sections.conversation_summary),
                "known_facts": list(sections.known_facts),
                "prior_work": list(sections.prior_work),
                "attachments": list(sections.attachments),
                "external_evidence": list(sections.external_evidence),
                "unresolved_references": list(sections.unresolved_references),
            },
            "trace": {
                "candidate_count": trace.candidate_count,
                "selected_count": trace.selected_count,
                "final_token_count": trace.final_token_count,
                "summary_updated": trace.summary_updated,
                "reference_resolution": {
                    "asset_ids": list(trace.reference_resolution.asset_ids),
                    "historical_asset_ids": list(
                        trace.reference_resolution.historical_asset_ids
                    ),
                    "needs_working_context": trace.reference_resolution.needs_working_context,
                    "unresolved_references": list(
                        trace.reference_resolution.unresolved_references
                    ),
                },
            },
        }

    def _planner_input(self, state, compiled_context) -> PlannerInput:
        harness = state["harness"]
        return PlannerInput(
            compiled_context=compiled_context,
            state_view=PlannerStateView(
                original_goal=harness["original_goal"],
                iteration=harness["iteration"],
                observations=[
                    RunObservation.model_validate(value)
                    for value in harness["observations"]
                ],
                last_error=(
                    None
                    if harness.get("last_error") is None
                    else harness["last_error"]
                ),
            ),
            tool_specs=self.tool_specs,
        )

    async def _finalize(
        self,
        *,
        command: StartRunCommand,
        state: HarnessGraphState,
        compiled_context,
        final_answer: str | None,
        terminal_status: HarnessStatusType = HarnessStatusType.COMPLETED,
    ) -> LoopRunResult:
        if not final_answer:
            raise ValueError("收口答案不能为空")
        state["harness"]["final_answer"] = final_answer
        state = self._transition(
            state,
            status=HarnessStatusType.RUNNING,
            phase=LoopPhaseStatusType.FINALIZATION,
            terminal_intent=terminal_status.value,
        )
        await self.run_store.save(command.run_ref, state)
        final_output_type, final_output_ref = (
            self._final_output(state)
            if terminal_status is HarnessStatusType.COMPLETED
            else ("text", None)
        )
        # 收口失败抛出 FinalizationFailure：运行现场保持 running/finalization，
        # 由 reconcile 恢复；这里不做任何降级或二次终态提交。
        finalization = await self.finalization_service.finalize(
            FinalizationInput(
                run_ref=command.run_ref,
                user_query=command.input_text,
                compiled_context=compiled_context,
                final_answer=final_answer,
                terminal_status=terminal_status,
                error_message=(
                    str((state["harness"].get("last_error") or {}).get("message", ""))
                    if terminal_status is not HarnessStatusType.COMPLETED
                    else ""
                ),
                asset_ids=list(command.asset_ids),
                final_output_type=final_output_type,
                final_output_ref=final_output_ref,
            )
        )
        # 终态已成功持久化，发布一次本次运行的终态事件
        event_type = {
            HarnessStatusType.COMPLETED: EventType.RUN_COMPLETED,
            HarnessStatusType.FAILED: EventType.RUN_FAILED,
            HarnessStatusType.TIMEOUT: EventType.RUN_TIMEOUT,
            HarnessStatusType.CANCELLED: EventType.RUN_CANCELLED,
        }[finalization.status]
        self.event_writer.emit(
            event_type,
            phase=LoopPhaseStatusType.FINALIZATION,
            iteration=finalization.iteration,
            payload={
                "status": finalization.status.value,
                "final_answer": finalization.final_answer,
                "final_output_type": finalization.final_output_type,
                "final_output_ref": finalization.final_output_ref,
                "error_code": (
                    None
                    if finalization.last_error is None
                    else finalization.last_error.code
                ),
                "error_message": (
                    None
                    if finalization.last_error is None
                    else finalization.last_error.message
                ),
            },
        )
        return LoopRunResult(
            run_ref=command.run_ref,
            status=finalization.status,
            phase=LoopPhaseStatusType.FINALIZATION,
            iteration=finalization.iteration,
            finalization_result=finalization,
            last_error=finalization.last_error,
        )

    async def _save_running_state(
        self, command: StartRunCommand, state: HarnessGraphState
    ) -> None:
        """保存非终态现场，并让保存操作服从运行级 deadline。"""
        await self._await_with_deadline(
            self.run_store.save(command.run_ref, state),
            state,
        )

    def _final_output(self, state: HarnessGraphState) -> tuple[str, str | None]:
        """按 ToolSpec.result_kind 回溯本轮结构化输出，不按工具名判断。"""
        report_specs = {
            spec.name: spec
            for spec in self.tool_specs
            if spec.result_kind == "report"
        }
        for value in reversed(state["harness"]["observations"]):
            observation = RunObservation.model_validate(value)
            spec = report_specs.get(observation.tool_name)
            if (
                spec is not None
                and observation.result_ref
                and observation.status
                in {ResultStatus.SUCCESS, ResultStatus.PARTIAL}
            ):
                return spec.artifact_kind or spec.name, observation.result_ref
        return "text", None

    def _new_state(self, command: StartRunCommand) -> HarnessGraphState:
        now = datetime.now(UTC)
        # 初始 status/phase 不在这里写：新运行起点（RUNNING/START_RUN）由 state.py 的
        # _DEFAULTS 统一给出；controller 只补 run 身份和逐次可变的值，避免两处真相
        harness = new_harness_control_state(
            original_goal=command.input_text,
            max_iterations=self.max_iterations,
            started_at=now.isoformat(),
            deadline_at=(now + timedelta(seconds=self.run_timeout_seconds)).isoformat(),
        )
        return {
            "input_text": command.input_text,
            "original_question": command.input_text,
            "user_id": command.run_ref.user_id,
            "conversation_id": command.run_ref.conversation_id,
            "thread_id": command.run_ref.thread_id,
            "turn_id": command.run_ref.turn_id,
            "run_id": command.run_ref.run_id,
            "project_id": command.project_id,
            "asset_ids": list(command.asset_ids),
            "harness": harness,
        }

    @staticmethod
    def _run_ref_from_state(state: HarnessGraphState):
        """从可恢复现场重建完整身份，不接受状态之外的覆盖。"""
        from app.agent.state_result_store.contracts import HarnessRunRef

        return HarnessRunRef(
            user_id=state["user_id"],
            conversation_id=state["conversation_id"],
            thread_id=state["thread_id"],
            turn_id=state["turn_id"],
            run_id=state["run_id"],
        )

    @staticmethod
    def _transition(
        state: HarnessGraphState,
        *,
        status: HarnessStatusType,
        phase: LoopPhaseStatusType,
        terminal_intent: str | None = None,
    ) -> HarnessGraphState:
        """harness 现场迁移一步并回写整个 state；迁移规则全在 store 层把关。"""

        # 这里只做子字典搬运：transition_harness_state 只认识 harness 字典，
        # "外层 state 挂着 harness"的布局知识收敛在这一处（13 个调用点不重复）。
        # 原地更新 + 返回：赋值是实际效果，返回让调用点的
        # `state = self._transition(...)` 显式表达"state 在此推进一步"。
        state["harness"] = transition_harness_state(
            state["harness"],
            status=status,
            phase=phase,
            terminal_intent=terminal_intent,
        )
        return state


__all__ = ["LoopController"]
