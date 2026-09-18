"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  AlertCircle,
  Braces,
  CheckCircle2,
  ChevronRight,
  Circle,
  ClipboardList,
  Loader2,
  Timer,
  XCircle,
} from "lucide-react";
import { Dialog, DialogContent, DialogTitle } from "../ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../ui/tabs";

export interface StreamEvent {
  type: string;
  step?: string;
  node?: string;
  status?: string;
  task_id?: string | number;
  [key: string]: unknown;
}

export type RunStatus = "pending" | "running" | "success" | "partial" | "failed";

export interface DebugEvent {
  key: string;
  sequence: number;
  type: string;
  step: string;
  sourceStep: string;
  node: string;
  taskId?: string;
  status?: RunStatus;
  iteration: number;
  receivedAt: string;
  payload: Record<string, unknown>;
  truncatedFields?: string[];
}

interface DisplayEvent {
  key: string;
  label: string;
  event: DebugEvent;
  status: RunStatus | undefined;
}

interface NodeGroup {
  node: string;
  status: RunStatus;
  latest: DebugEvent;
  events: DisplayEvent[];
}

export interface TaskSummary {
  task_id: string;
  status: RunStatus;
  question?: string;
  resolved_question?: string;
  purpose?: string;
  depends_on: string[];
  node?: string;
  phase?: string;
  error?: string;
  events: DebugEvent[];
}

interface TaskGroup {
  taskId: string;
  status: RunStatus;
  task: TaskSummary;
  events: DebugEvent[];
  steps: Array<{
    label: string;
    status: RunStatus;
    latest: DebugEvent;
    nodes: NodeGroup[];
    display: DisplayEvent[];
  }>;
}

interface StepGroup {
  step: string;
  status: RunStatus;
  latest: DebugEvent;
  events: DebugEvent[];
  nodes: NodeGroup[];
  tasks: TaskGroup[];
}

interface RoundGroup {
  round: number;
  label: string;
  status: RunStatus;
  summary: string;
  steps: StepGroup[];
}

const DEBUG_ROW_LIMIT = 1000;
const DEBUG_ARRAY_FIELDS = new Set(["rows", "data", "display_sql_result", "preview_rows"]);
const STREAM_EVENT_TYPES = new Set(["reasoning_chunk", "llm_chunk"]);
const RESULT_EVENT_TYPES = new Set([
  "reasoning_result",
  "llm_result",
  "analysis_task_resolved",
  "analysis_task_result",
  "report_plan_result",
  "rendered_report",
]);
const STEP_ORDER = [
  "开始执行", "构建上下文", "任务划分", "提交动作", "执行工具",
  "等待用户确认", "运行结果",
];

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function statusOf(value: unknown): RunStatus | undefined {
  if (value === "completed") return "success";
  if (value === "created") return "pending";
  if (value === "waiting_confirmation" || value === "needs_user") return "partial";
  if (
    value === "temporary_error"
    || value === "unrecoverable_error"
    || value === "timeout"
    || value === "cancelled"
  ) return "failed";
  if (value === "pending" || value === "running" || value === "success" || value === "partial" || value === "failed") return value;
  return undefined;
}

function terminal(status: RunStatus | undefined) {
  return status === "success" || status === "partial" || status === "failed";
}

/** 运行级中断事件：失败/超时/取消/流断连。 */
export function runFailed(events: DebugEvent[]) {
  return events.some((event) =>
    event.type === "run.failed"
    || event.type === "run.timeout"
    || event.type === "run.cancelled"
    || event.type === "stream.failed");
}

/** 服务端 timestamp 优先（历史 trace 的 receivedAt 均为补拉时刻），缺省退回本地接收时间。 */
export function eventTimestampMs(event: DebugEvent) {
  const server = typeof event.payload.timestamp === "string" ? Date.parse(event.payload.timestamp) : NaN;
  if (Number.isFinite(server)) return server;
  const local = Date.parse(event.receivedAt);
  return Number.isFinite(local) ? local : NaN;
}

/** 毫秒时长转人类可读文案；无效或非正数返回空串。 */
export function formatDuration(ms: number) {
  if (!Number.isFinite(ms) || ms <= 0) return "";
  const seconds = ms / 1000;
  if (seconds < 1) return "<1 秒";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`;
}

function mainStep(sourceStep: string, type: string) {
  if (type === "run.started") return "开始执行";
  if (type === "run.completed" || type === "run.failed" || type === "run.timeout" || type === "run.cancelled" || type === "run.result" || type === "stream.failed") return "运行结果";
  if (type.startsWith("context.")) return "构建上下文";
  if (type.startsWith("planner.")) return "任务划分";
  if (type === "action.committed") return "提交动作";
  if (type.startsWith("tool.")) return "执行工具";
  if (type.startsWith("confirmation.")) return "等待用户确认";
  if (type === "question_route") return "判断问题路由";
  if (sourceStep === "问题路由" || sourceStep === "判断问题路由") return "判断问题路由";
  if (sourceStep === "抽取关键词" || sourceStep === "提取关键词") return "提取关键词";
  if (sourceStep.startsWith("召回columns")) return "召回字段";
  if (sourceStep.startsWith("召回tables")) return "召回表";
  if (sourceStep.startsWith("召回metrics")) return "召回指标";
  if (sourceStep.startsWith("召回dimension_values")) return "召回维度值";
  if (sourceStep.startsWith("过滤指标")) return "过滤指标";
  if (sourceStep.startsWith("过滤表")) return "过滤表";
  if (sourceStep.startsWith("补全过滤后的上下文") || sourceStep.startsWith("补充 SQL 生成上下文")) return "整理 SQL 上下文";
  if (sourceStep.startsWith("生成 SQL")) return "生成 SQL";
  if (sourceStep.startsWith("执行 SQL")) return "执行 SQL";
  if (sourceStep.startsWith("增强查询结果")) return "增强查询结果";
  if (sourceStep.startsWith("执行分析任务") || type === "analysis_task_phase" || type === "analysis_task_resolved" || type === "analysis_task_result") return "执行分析任务";
  if (sourceStep.startsWith("汇总分析证据") || sourceStep.startsWith("汇总全部分析证据")) return "汇总全部分析证据";
  if (sourceStep.startsWith("生成报告规划")) return "生成报告规划";
  if (sourceStep === "生成最终报告" || sourceStep === "渲染最终报告") return "渲染最终报告";
  return sourceStep || "未命名步骤";
}

function moduleNode(event: StreamEvent, type: string, payload: Record<string, unknown>) {
  const source = typeof event.source === "string" ? event.source.trim() : "";
  if (type.startsWith("context.")) return "context_engine";
  if (type.startsWith("planner.")) return "planning_agent";
  if (type === "action.committed" || type.startsWith("confirmation.")) return "loop_controller";
  if (type.startsWith("run.") || type === "stream.failed") return "loop_controller";
  if (type.startsWith("tool.")) {
    const toolName = event.tool_name ?? payload.tool_name;
    if (typeof toolName === "string" && toolName.trim()) return toolName.trim();
    if (source && source !== "harness") return source;
    return "tool_runtime";
  }
  if (source && source !== "harness") return source;
  return typeof event.node === "string" && event.node.trim() ? event.node.trim() : "unknown_node";
}

function safePayload(value: unknown, truncated: string[], path = ""): unknown {
  if (Array.isArray(value)) {
    const field = path.split(".").pop() || "";
    if (path && DEBUG_ARRAY_FIELDS.has(field) && value.length > DEBUG_ROW_LIMIT) {
      truncated.push(path + ": " + value.length + " -> " + DEBUG_ROW_LIMIT);
      return value.slice(0, DEBUG_ROW_LIMIT).map((item, index) => safePayload(item, truncated, path + "[" + index + "]"));
    }
    return value.map((item, index) => safePayload(item, truncated, path + "[" + index + "]"));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, safePayload(item, truncated, path ? path + "." + key : key)]));
  }
  return value;
}

function makeEvent(event: StreamEvent): DebugEvent {
  const type = event.type.trim();
  const sourceStep = typeof event.step === "string" && event.step.trim() ? event.step.trim() : "未命名步骤";
  const taskId = typeof event.task_id === "string" || typeof event.task_id === "number" ? String(event.task_id) : undefined;
  // Harness 事件自带 0-based 迭代轮次；老 graph 事件没有该字段，统一归入第 1 轮。
  const iteration = typeof event.iteration === "number" ? event.iteration : 0;
  const phase = typeof event.phase === "string" ? event.phase : "";
  const truncated: string[] = [];
  const payload = safePayload(event, truncated) as Record<string, unknown>;
  if (truncated.length) {
    payload.debug_truncated = true;
    payload.debug_truncated_fields = truncated;
  }
  const resolvedNode = moduleNode(event, type, payload);
  return {
    key: type + ":" + sourceStep + ":" + resolvedNode + ":" + phase + ":" + (taskId || ""),
    sequence: -1,
    type,
    step: mainStep(sourceStep, type),
    sourceStep,
    node: resolvedNode,
    taskId,
    status: statusOf(event.status),
    iteration,
    receivedAt: new Date().toISOString(),
    payload,
    truncatedFields: truncated.length ? truncated : undefined,
  };
}

export function appendExecutionEvent(events: DebugEvent[], event: StreamEvent): DebugEvent[] {
  if (!event || typeof event.type !== "string" || !event.type.trim()) return events;
  const nextEvent = makeEvent(event);
  // 调试页的“全部事件”必须保留每一次 SSE 到达记录，不能用状态更新覆盖旧事件。
  // 聚合视图会在 displayEvents/currentEvents 中自行折叠 running 与流式片段。
  return events.concat({
    ...nextEvent,
    sequence: events.length,
    key: nextEvent.key + ":raw:" + String(events.length),
  });
}

const CONTEXT_PROGRESS_EVENT_TYPES = new Set([
  "context.memory_retrieved",
  "context.knowledge_retrieved",
  "context.context_compiled",
]);

/** Harness 事件名自带生命周期语义；缺少显式 status 时按类型推导展示状态。 */
function statusFromEventType(type: string): RunStatus | undefined {
  if (
    // run.started 到达即代表“开始执行”模块完成，后续阶段由各自事件驱动。
    type === "run.started"
    || type.endsWith(".completed")
    || type === "action.committed"
    || type === "confirmation.resolved"
    || RESULT_EVENT_TYPES.has(type)
  ) {
    return "success";
  }
  if (
    type.endsWith(".started")
    || type.endsWith(".retrying")
    || type === "tool.progress"
    || type === "confirmation.required"
    || CONTEXT_PROGRESS_EVENT_TYPES.has(type)
  ) {
    return "running";
  }
  return undefined;
}

/** 事件条目的展示状态：显式终态优先，其次按类型与同族生命周期推导。 */
function itemStatus(event: DebugEvent, siblings: DebugEvent[]): RunStatus | undefined {
  if (event.status && event.status !== "running") return event.status;
  if (event.type === "error" || event.type.endsWith(".failed") || event.type === "run.timeout" || event.type === "run.cancelled") return "failed";
  // 显式 running 不能短路同族终态判断：进度标记在所属工具完成后应随同族事件收敛。
  const derived = statusFromEventType(event.type) ?? (event.status === "running" ? "running" : undefined);
  if (derived === "running") {
    // 同族生命周期在其之后出现终态事件时，started/进度标记按已完成展示，避免运行结束后仍显示执行中。
    const family = event.type.split(".")[0];
    const concluded = siblings.some(
      (item) => item.sequence > event.sequence
        && (item.type === `${family}.completed` || item.type === `${family}.failed` || item.type === `${family}.resolved`),
    );
    if (concluded) return "success";
  }
  return derived;
}

function statusForEvents(events: DebugEvent[], fallback?: RunStatus, runCompleted = false): RunStatus {
  let latestStatus = fallback;
  for (const event of events.slice().sort((left, right) => eventOrder(left) - eventOrder(right))) {
    if (
      event.type === "error"
      || event.type.endsWith(".failed")
      || event.type === "run.timeout"
      || event.type === "run.cancelled"
    ) {
      latestStatus = "failed";
    } else if (event.status) {
      latestStatus = event.status;
    } else {
      const derived = statusFromEventType(event.type);
      if (derived) latestStatus = derived;
    }
  }
  if (runCompleted && events.length && latestStatus !== "failed") return "success";
  // 运行已到终态（取消/超时/失败）：已完成模块保持终态，仍在执行的模块标记为中断（partial）。
  const interrupted = events.some((event) => event.type === "run.cancelled" || event.type === "run.timeout" || event.type === "run.failed");
  if (interrupted) return latestStatus === "running" ? "partial" : latestStatus || "pending";
  return latestStatus || "pending";
}

function taskStatus(events: DebugEvent[]): RunStatus {
  const result = events.filter((event) => customEventOf(event, "analysis_task_result")).at(-1);
  if (result && result.status === "failed") return "failed";
  if (result && result.status === "partial") return "partial";
  if (result) return "success";
  if (events.some((event) => event.type === "error")) return "failed";
  return events.length ? "running" : "pending";
}

function eventScope(event: DebugEvent) {
  const phase = typeof event.payload.phase === "string"
    ? event.payload.phase
    : customEventOf(event, "analysis_task_resolved")
      ? "解析依赖结果"
      : "";
  return eventBaseScope(event) + "::" + phase;
}

function eventBaseScope(event: DebugEvent) {
  return event.step + "::" + (event.taskId || "__main__") + "::" + event.node;
}

function eventOrder(event: DebugEvent) {
  return event.sequence >= 0 ? event.sequence : Number.MAX_SAFE_INTEGER;
}

function currentEvents(events: DebugEvent[]) {
  // 分析任务的最终结果没有 phase，必须覆盖同一任务下此前各阶段的 running。
  const terminalTaskScopes = new Set(
    events
      .filter((event) => customEventOf(event, "analysis_task_result"))
      .map(eventBaseScope),
  );
  const terminalScopes = new Set(
    events
      .filter((event) =>
        event.type === "error" ||
        terminal(event.status) ||
        customEventOf(event, "analysis_task_resolved"),
      )
      .map(eventScope),
  );
  const latestRunning = new Map<string, DebugEvent>();
  for (const event of events) {
    if (event.status === "running" && !terminalTaskScopes.has(eventBaseScope(event)) && !terminalScopes.has(eventScope(event))) {
      // 同一分析任务的不同 phase 是一条执行链，只保留最新的运行阶段。
      const runningKey = customEventOf(event, "analysis_task_phase") ? eventBaseScope(event) : eventScope(event);
      latestRunning.set(runningKey, event);
    }
  }
  return events.filter((event) => {
    if (event.status !== "running") return true;
    const baseScope = eventBaseScope(event);
    const scope = eventScope(event);
    const runningKey = customEventOf(event, "analysis_task_phase") ? baseScope : scope;
    return !terminalTaskScopes.has(baseScope) && !terminalScopes.has(scope) && latestRunning.get(runningKey) === event;
  });
}

function displayLabel(event: DebugEvent) {
  const phase = typeof event.payload.phase === "string" ? " · " + event.payload.phase : "";
  if (event.type === "run.started") return "运行开始";
  if (event.type === "run.completed") return "运行完成";
  if (event.type === "run.failed") return "运行失败";
  if (event.type === "run.timeout") return "运行超时";
  if (event.type === "run.cancelled") return "运行已取消";
  if (event.type === "run.result") return "运行结果";
  if (event.type === "stream.failed") return "流式连接失败";
  if (event.type === "context.started") return "开始构建上下文";
  if (event.type === "context.memory_retrieved") return "读取记忆";
  if (event.type === "context.knowledge_retrieved") return "召回语义知识";
  if (event.type === "context.context_compiled") return "上下文已组装";
  if (event.type === "context.plan") return "上下文召回规划";
  if (event.type === "context.completed") return "上下文构建完成";
  if (event.type === "planner.started") return "开始任务划分";
  if (event.type === "planner.completed") return "任务划分完成";
  if (event.type === "planner.retrying") return "任务划分重试";
  if (event.type === "planner.failed") return "任务划分失败";
  if (event.type === "action.committed") return "动作已提交";
  if (event.type === "tool.started") return "工具开始执行";
  if (event.type === "tool.progress") {
    // custom_type 是 writer.custom 归一化前的原始事件类型；思考/正文流沿用原语义命名，不展示片段计数。
    const kind = progressKind(event);
    if (kind === "reasoning") return "思考过程";
    if (kind === "llm") return "模型输出";
    const customType = event.payload.custom_type;
    if (customType === "question_route") return "判断问题路由";
    if (customType === "analysis_plan") return "分析计划";
    if (customType === "analysis_task_phase") return "分析阶段" + phase;
    if (customType === "analysis_task_resolved") return "依赖结果解析";
    if (customType === "analysis_task_result") return "分析任务结果";
    if (customType === "report_plan_result") return "报告规划结果";
    if (customType === "rendered_report") return "最终报告";
    const count = typeof event.payload.compacted_count === "number"
      ? event.payload.compacted_count
      : 1;
    return count > 1 ? `工具执行进度（${count} 条内部事件）` : "工具执行进度";
  }
  if (event.type === "tool.completed") return "工具执行完成";
  if (event.type === "tool.failed") return "工具执行失败";
  if (event.type === "confirmation.required") return "等待用户确认";
  if (event.type === "confirmation.resolved") return "用户确认已处理";
  if (event.type === "reasoning_result") return "思考过程" + phase;
  if (event.type === "reasoning_chunk") {
    const chunkCount = typeof event.payload.chunk_count === "number" ? event.payload.chunk_count : 0;
    return (chunkCount > 1 ? "思考过程" : "思考过程片段") + (chunkCount > 1 ? "（" + chunkCount + " 个片段）" : "") + phase;
  }
  if (event.type === "llm_result") return "模型原始输出" + phase;
  if (event.type === "llm_chunk") {
    const chunkCount = typeof event.payload.chunk_count === "number" ? event.payload.chunk_count : 0;
    return (chunkCount > 1 ? "模型输出" : "模型输出片段") + (chunkCount > 1 ? "（" + chunkCount + " 个片段）" : "") + phase;
  }
  if (event.type === "analysis_task_phase") return "分析阶段" + phase;
  if (event.type === "analysis_task_resolved") return "依赖结果解析";
  if (event.type === "analysis_task_result") return "分析任务结果";
  if (event.type === "error") return "错误信息";
  if (event.type === "report_plan_result") return "报告规划结果";
  if (event.type === "rendered_report") return "最终报告";
  if (event.status === "success") return event.step + "结果";
  if (event.status === "failed") return event.step + "失败信息";
  if (event.status === "partial") return event.step + "部分结果";
  if (event.status === "running") return event.step + "执行中";
  return event.step + "返回";
}

function displayEvents(events: DebugEvent[]): DisplayEvent[] {
  const hasReasoningResult = events.some((event) => event.type === "reasoning_result");
  const hasLlmResult = events.some((event) => event.type === "llm_result");
  const hasNonProgress = events.some((event) => event.type !== "progress");
  // 节点内已有思考/正文流时，裸进度标记只是重复的“已开始”信号，直接隐藏。
  const hasStreamProgress = events.some((event) => event.type === "tool.progress" && progressKind(event) !== "progress");
  const visible: DisplayEvent[] = [];
  for (const event of events) {
    if (hasNonProgress && event.type === "progress") continue;
    if (hasStreamProgress && event.type === "tool.progress" && progressKind(event) === "progress") continue;
    if (hasReasoningResult && event.type === "reasoning_chunk") continue;
    if (hasLlmResult && event.type === "llm_chunk") continue;
    // 同一个展示标题可能对应多个返回事件，不能用标题作为 Map key 覆盖前面的结果。
    visible.push({ key: event.key + ":display", label: displayLabel(event), event, status: itemStatus(event, events) });
  }
  return visible;
}

function streamScope(event: DebugEvent) {
  const phase = typeof event.payload.phase === "string" ? event.payload.phase : "";
  return event.type + "::" + event.node + "::" + (event.taskId || "__main__") + "::" + phase;
}

function streamText(event: DebugEvent) {
  return typeof event.payload.chunk === "string" ? event.payload.chunk : "";
}

/** 工具进度按归一化前的原始事件类型分桶：思考/正文各自连同结果事件聚合，普通进度单独合并。 */
export function progressKind(event: DebugEvent) {
  const customType = event.payload.custom_type;
  if (customType === "reasoning_chunk" || customType === "reasoning_result") return "reasoning";
  if (customType === "llm_chunk" || customType === "llm_result") return "llm";
  return "progress";
}

const ROUTE_MODE_LABELS: Record<string, string> = {
  daily_chat: "日常聊天",
  single_query: "单次查询",
  analysis: "数据分析",
  clarification: "需要澄清",
};

/** custom 事件在 harness 链路被归一化为 tool.progress + custom_type；旧 agent 链路保留原始 type。两种形态都要识别。 */
export function customEventOf(event: DebugEvent, name: string) {
  return event.type === name
    || (event.type === "tool.progress" && event.payload.custom_type === name);
}

function formatTokens(count: number) {
  return count >= 1000 ? `${(count / 1000).toFixed(1)}k tokens` : `${count} tokens`;
}

function compactAllEvents(events: DebugEvent[]): DebugEvent[] {
  const compacted: DebugEvent[] = [];
  const compactIndexes = new Map<string, number>();

  for (const event of events) {
    const isStream = STREAM_EVENT_TYPES.has(event.type);
    const isToolProgress = event.type === "tool.progress";
    if (!isStream && !isToolProgress) {
      compacted.push(event);
      continue;
    }

    // 结果事件（*_result）到达即代表该流式分桶完成；否则合并项会一直停留在 running。
    const incomingStatus: RunStatus | undefined = isToolProgress && String(event.payload.custom_type || "").endsWith("_result")
      ? "success"
      : event.status;
    const scope = isToolProgress
      ? "tool.progress::"
        + progressKind(event)
        + "::"
        + String(event.payload.action_id || event.payload.source || event.node)
        + "::"
        + String(event.payload.phase || event.step || "")
      : streamScope(event);
    const existingIndex = compactIndexes.get(scope);
    if (existingIndex === undefined) {
      const chunk = streamText(event);
      compacted.push({
        ...event,
        status: incomingStatus,
        key: event.key + ":compact",
        payload: {
          ...event.payload,
          chunk: chunk || event.payload.chunk,
          combined_text: chunk,
          chunk_count: 1,
          ...(isToolProgress ? { compacted_count: 1 } : {}),
          first_sequence: event.sequence,
          last_sequence: event.sequence,
        },
      });
      compactIndexes.set(scope, compacted.length - 1);
      continue;
    }

    const current = compacted[existingIndex];
    const currentText = typeof current.payload.combined_text === "string" ? current.payload.combined_text : "";
    const chunk = streamText(event);
    const chunkCount = typeof current.payload.chunk_count === "number" ? current.payload.chunk_count : 1;
    const compactedCount = typeof current.payload.compacted_count === "number"
      ? current.payload.compacted_count
      : 1;
    // 普通进度桶先收到裸 progress、后收到同名业务事件时，用业务事件的 custom_type 命名合并项。
    const currentCustomType = String(current.payload.custom_type || "progress");
    const incomingCustomType = String(event.payload.custom_type || "progress");
    const mergedCustomType = currentCustomType === "progress" && incomingCustomType !== "progress"
      ? incomingCustomType
      : currentCustomType;
    compacted[existingIndex] = {
      ...current,
      sequence: event.sequence,
      receivedAt: event.receivedAt,
      status: incomingStatus || current.status,
      payload: {
        ...current.payload,
        chunk: currentText + chunk,
        combined_text: currentText + chunk,
        chunk_count: chunkCount + 1,
        ...(isToolProgress ? { compacted_count: compactedCount + 1 } : {}),
        ...(mergedCustomType !== currentCustomType ? { custom_type: mergedCustomType } : {}),
        last_sequence: event.sequence,
      },
    };
  }

  return compacted;
}

function orderOf(step: string) {
  const index = STEP_ORDER.indexOf(step);
  return index === -1 ? STEP_ORDER.length : index;
}

function nodeGroups(events: DebugEvent[], fallback?: RunStatus, runCompleted = false): NodeGroup[] {
  const grouped = new Map<string, DebugEvent[]>();
  for (const event of events) grouped.set(event.node, [...(grouped.get(event.node) || []), event]);
  return Array.from(grouped.entries()).map(([node, nodeEvents]) => ({
    node,
    status: statusForEvents(nodeEvents, fallback, runCompleted),
    latest: nodeEvents[nodeEvents.length - 1],
    // 与「全部事件」同源压缩：节点内的思考/正文流式片段、工具进度合并为少数几条，避免逐 chunk 刷屏。
    events: displayEvents(compactAllEvents(nodeEvents)),
  })).sort((left, right) => eventOrder(left.latest) - eventOrder(right.latest));
}

export function buildTasks(events: DebugEvent[]): TaskSummary[] {
  const summaries = new Map<string, TaskSummary>();
  for (const event of events) {
    if (!customEventOf(event, "analysis_plan") || !Array.isArray(event.payload.tasks)) continue;
    for (const item of event.payload.tasks) {
      const task = record(item);
      const taskId = typeof task.task_id === "string" || typeof task.task_id === "number" ? String(task.task_id) : "";
      if (!taskId || summaries.has(taskId)) continue;
      summaries.set(taskId, {
        task_id: taskId,
        status: "pending",
        question: typeof task.question === "string" ? task.question : undefined,
        purpose: typeof task.purpose === "string" ? task.purpose : undefined,
        depends_on: Array.isArray(task.depends_on) ? task.depends_on.filter((value): value is string => typeof value === "string") : [],
        events: [],
      });
    }
  }
  for (const event of events) {
    if (!event.taskId) continue;
    const summary = summaries.get(event.taskId) || { task_id: event.taskId, status: "pending" as RunStatus, depends_on: [], events: [] };
    summary.events = summary.events.concat(event);
    if (typeof event.payload.question === "string") summary.question = event.payload.question;
    if (typeof event.payload.resolved_question === "string") summary.resolved_question = event.payload.resolved_question;
    if (typeof event.payload.purpose === "string") summary.purpose = event.payload.purpose;
    if (typeof event.payload.phase === "string") summary.phase = event.payload.phase;
    if (Array.isArray(event.payload.depends_on)) summary.depends_on = event.payload.depends_on.filter((value): value is string => typeof value === "string");
    if (typeof event.payload.error === "string") summary.error = event.payload.error;
    summary.node = event.node;
    summaries.set(event.taskId, summary);
  }
  return Array.from(summaries.values()).map((summary) => {
    const status = taskStatus(summary.events);
    return { ...summary, status, events: currentEvents(summary.events) };
  });
}

function buildTaskGroups(tasks: TaskSummary[], runCompleted = false): TaskGroup[] {
  return tasks.map((task) => {
    const events = currentEvents(task.events).filter((event) => event.type !== "progress");
    const grouped = new Map<string, DebugEvent[]>();
    for (const event of events) {
      const label = taskStepLabel(event);
      grouped.set(label, [...(grouped.get(label) || []), event]);
    }
    const steps = Array.from(grouped.entries())
      .map(([label, stepEvents]) => ({
        label,
        status: statusForEvents(stepEvents, task.status),
        latest: stepEvents[stepEvents.length - 1],
        nodes: nodeGroups(stepEvents, task.status, runCompleted),
        display: displayEvents(stepEvents),
      }))
      .sort((left, right) => eventOrder(left.latest) - eventOrder(right.latest));
    return { taskId: task.task_id, status: task.status, task, events, steps };
  });
}

function taskStepLabel(event: DebugEvent) {
  if (customEventOf(event, "analysis_task_phase") && typeof event.payload.phase === "string") {
    return event.payload.phase;
  }
  if (customEventOf(event, "analysis_task_result")) return "分析任务结果";
  if (customEventOf(event, "analysis_task_resolved")) return "依赖结果解析";
  return event.step;
}

function aggregateTaskStatus(tasks: TaskSummary[]): RunStatus {
  if (tasks.some((task) => task.status === "running" || task.status === "pending")) return "running";
  const failed = tasks.filter((task) => task.status === "failed").length;
  const partial = tasks.some((task) => task.status === "partial");
  const successful = tasks.some((task) => task.status === "success");
  if (failed && (partial || successful)) return "partial";
  if (failed) return "failed";
  if (partial) return "partial";
  return "success";
}

function buildStepGroups(events: DebugEvent[], tasks: TaskSummary[]): StepGroup[] {
  const runCompleted = events.some((event) => event.type === "run.completed");
  const grouped = new Map<string, DebugEvent[]>();
  const publicEvents = events.filter((item) => !STREAM_EVENT_TYPES.has(item.type) && !item.taskId);
  for (const event of publicEvents) {
    const label = event.step;
    grouped.set(label, [...(grouped.get(label) || []), event]);
  }
  const result: StepGroup[] = Array.from(grouped.entries()).map(([step, stepEvents]) => {
    const visible = currentEvents(stepEvents);
    return {
      step,
      status: statusForEvents(visible, undefined, runCompleted),
      latest: visible[visible.length - 1] || stepEvents[stepEvents.length - 1],
      events: visible,
      nodes: nodeGroups(visible, undefined, runCompleted),
      tasks: [],
    };
  });
  if (tasks.length) {
    result.push({
      step: "执行分析任务",
      status: aggregateTaskStatus(tasks),
      latest: taskOverview(tasks),
      events: [],
      nodes: [],
      tasks: buildTaskGroups(tasks, runCompleted),
    });
  }
  return result.sort((left, right) => orderOf(left.step) - orderOf(right.step));
}

function buildRoundGroups(events: DebugEvent[], tasks: TaskSummary[]): RoundGroup[] {
  const runCompleted = events.some((event) => event.type === "run.completed");
  const grouped = new Map<number, DebugEvent[]>();
  for (const event of events) grouped.set(event.iteration, [...(grouped.get(event.iteration) || []), event]);
  const rounds = Array.from(grouped.keys()).sort((left, right) => left - right);
  const taskRound = events.find((event) => event.type === "analysis_plan")?.iteration ?? rounds[rounds.length - 1] ?? 0;
  return rounds.map((round) => {
    const roundEvents = grouped.get(round) || [];
    const steps = buildStepGroups(roundEvents, round === taskRound ? tasks : []);
    return {
      round,
      label: `第 ${round + 1} 轮`,
      status: statusForEvents(
        currentEvents(roundEvents.filter((item) => !STREAM_EVENT_TYPES.has(item.type) && !item.taskId)),
        undefined,
        runCompleted,
      ),
      summary: steps.map((step) => step.step).join(" → "),
      steps,
    };
  });
}

function taskOverview(tasks: TaskSummary[]): DebugEvent {
  const status = aggregateTaskStatus(tasks);
  return { key: "analysis-task-overview", sequence: -1, type: "analysis_task_overview", step: "执行分析任务", sourceStep: "执行分析任务", node: "execute_analysis", status, iteration: 0, receivedAt: new Date().toISOString(), payload: { type: "analysis_task_overview", message: "分析任务已按 task_id 归类。", task_count: tasks.length } };
}

function json(value: unknown) {
  const serialized = JSON.stringify(value, null, 2);
  return serialized === undefined
    ? String(value)
    : serialized.replaceAll("\\n", "\n").replaceAll("\\r", "\r").replaceAll("\\t", "\t");
}

export function StatusIcon({ status }: { status: RunStatus | undefined }) {
  if (status === "success") return <CheckCircle2 className="size-4 shrink-0 text-emerald-500" />;
  if (status === "failed") return <XCircle className="size-4 shrink-0 text-rose-500" />;
  if (status === "partial") return <AlertCircle className="size-4 shrink-0 text-amber-500" />;
  if (status === "pending") return <Loader2 className="size-4 shrink-0 animate-spin text-slate-300" />;
  if (status === "running") return <Loader2 className="size-4 shrink-0 animate-spin text-blue-500" />;
  return <Circle className="size-4 shrink-0 text-slate-300" />;
}

function EventDetails({ event, title, status }: { event: DebugEvent; title?: string; status?: RunStatus }) {
  return <details className="group rounded-md border border-slate-200 bg-white"><summary className="flex cursor-pointer list-none items-start gap-2 px-3 py-2.5"><ChevronRight className="mt-0.5 size-3.5 shrink-0 text-slate-400 transition-transform group-open:rotate-90" /><StatusIcon status={status ?? event.status ?? (event.type === "error" ? "failed" : undefined)} /><span className="min-w-0 flex-1"><span className="block break-words text-[11px] font-bold text-slate-700">{title || event.step}</span><span className="mt-0.5 block break-words text-[10px] text-slate-400">{displayLabel(event)} · 节点：{event.node}{event.taskId ? " · 任务：" + event.taskId : ""}</span></span><span className="shrink-0 text-[9px] text-slate-400">{new Date(event.receivedAt).toLocaleTimeString("zh-CN")}</span></summary><div className="border-t border-slate-100 px-3 pb-3 pt-2">{event.truncatedFields?.length ? <div className="mb-2 rounded bg-amber-50 px-2 py-1.5 text-[10px] leading-4 text-amber-800">调试预览已截断：{event.truncatedFields.join("；")}</div> : null}<pre className="max-h-[360px] overflow-auto whitespace-pre-wrap break-words rounded bg-slate-950 p-3 text-[10px] leading-5 text-slate-100">{json(event.payload)}</pre></div></details>;
}

function Drawer({ title, subtitle, status, children, trigger }: { title: string; subtitle?: string; status: RunStatus; children: ReactNode; trigger: (open: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false);
  return <><div>{trigger(() => setOpen(true))}</div><Dialog open={open} onOpenChange={setOpen}><DialogContent showCloseButton={false} style={{ width: "720px", maxWidth: "calc(100% - 24px)" }} className="fixed inset-y-0 right-0 left-auto top-0 z-50 grid h-full max-w-none translate-x-0 translate-y-0 gap-0 overflow-hidden rounded-none border-y-0 border-r-0 border-l border-slate-200 bg-slate-50 p-0 shadow-2xl data-open:slide-in-from-right data-closed:slide-out-to-right"><div className="min-h-0 overflow-y-auto"><div className="sticky top-0 z-20 border-b border-slate-200 bg-white px-4 py-3"><div className="flex items-start gap-2"><StatusIcon status={status} /><div className="min-w-0 flex-1"><DialogTitle className="break-words text-sm font-extrabold text-slate-800">{title}</DialogTitle>{subtitle && <div className="mt-1 break-words text-[10px] text-slate-400">{subtitle}</div>}</div><span className="shrink-0 text-[10px] font-bold text-slate-400">{status}</span></div></div><div className="space-y-3 p-4">{children}</div></div></DialogContent></Dialog></>;
}

function TaskCard({ group }: { group: TaskGroup }) {
  const task = group.task;
  const phase = task.node ? "当前节点：" + task.node : task.phase || (group.status === "pending" ? "等待执行" : group.status === "running" ? "执行中" : "已完成");
  return <Drawer title={group.taskId} subtitle={group.steps.length + " 个任务步骤 · " + phase} status={group.status} trigger={(open) => <button type="button" onClick={open} className="group w-full rounded-md border border-slate-200 bg-white px-3 py-3 text-left transition-colors hover:border-blue-300 hover:bg-blue-50"><span className="flex items-start gap-2"><ChevronRight className="mt-0.5 size-3.5 shrink-0 text-slate-400" /><StatusIcon status={group.status} /><span className="min-w-0 flex-1"><span className="block break-all text-[11px] font-extrabold text-slate-700">{group.taskId}</span><span className="mt-1 block truncate text-[10px] text-slate-400">{phase} · {group.steps.length} 个步骤</span></span><span className="shrink-0 text-[10px] text-slate-400">查看详情</span></span></button>}>{<TaskBody group={group} />}</Drawer>;
}

function TaskBody({ group }: { group: TaskGroup }) {
  const task = group.task;
  return <><div className="space-y-2 rounded-md border border-slate-200 bg-white px-3 py-2.5 text-[11px] leading-5">{task.question && <div><b className="text-slate-400">查询问题：</b><span className="text-slate-600">{task.question}</span></div>}{task.resolved_question && task.resolved_question !== task.question && <div><b className="text-slate-400">实际查询：</b><span className="text-slate-600">{task.resolved_question}</span></div>}{task.purpose && <div><b className="text-slate-400">任务用途：</b><span className="text-slate-600">{task.purpose}</span></div>}<div><b className="text-slate-400">依赖任务：</b><span className="text-slate-600">{task.depends_on.length ? task.depends_on.join("、") : "无"}</span></div>{task.node && <div><b className="text-slate-400">当前 Agent 节点：</b><span className="font-mono text-slate-600">{task.node}</span></div>}{task.error && <div className="rounded bg-rose-50 px-2 py-1 text-rose-700">失败原因：{task.error}</div>}</div>{group.steps.length ? <div className="space-y-2">{group.steps.map((step) => <details key={step.label} className="group rounded-md border border-slate-200 bg-white"><summary className="flex cursor-pointer list-none items-start gap-2 px-3 py-2.5"><ChevronRight className="mt-0.5 size-3.5 shrink-0 text-slate-400 transition-transform group-open:rotate-90" /><StatusIcon status={step.status} /><span className="min-w-0 flex-1"><span className="block text-[11px] font-bold text-slate-700">{step.label}</span><span className="mt-0.5 block text-[10px] text-slate-400">{step.nodes.length} 个 Agent 节点 · 最新：{displayLabel(step.latest)}</span></span><span className="shrink-0 text-[10px] text-slate-400">{step.status}</span></summary><div className="space-y-2 border-t border-slate-100 px-3 pb-3 pt-2">{step.nodes.length ? step.nodes.map((node) => <details key={node.node} className="group rounded-md border border-blue-100 bg-blue-50/30"><summary className="flex cursor-pointer list-none items-start gap-2 px-3 py-2.5"><ChevronRight className="mt-0.5 size-3.5 shrink-0 text-slate-400 transition-transform group-open:rotate-90" /><StatusIcon status={node.status} /><span className="min-w-0 flex-1"><span className="block text-[11px] font-bold text-slate-700">节点：{node.node}</span><span className="mt-0.5 block text-[10px] text-slate-400">{node.events.length} 个返回项</span></span></summary><div className="space-y-2 border-t border-blue-100 px-3 pb-3 pt-2">{node.events.map((item) => <EventDetails key={item.key} event={item.event} title={item.label} status={item.status} />)}</div></details>) : step.display.map((item) => <EventDetails key={item.key} event={item.event} title={item.label} status={item.status} />)}</div></details>)}</div> : <div className="py-5 text-center text-xs text-slate-400">该任务暂无详细返回</div>}</>;
}

function StepCard({ group }: { group: StepGroup }) {
  return <details open={group.status === "failed"} className="group rounded-lg border border-slate-200 bg-white"><summary className="flex cursor-pointer list-none items-start gap-2 px-3 py-3"><ChevronRight className="mt-0.5 size-3.5 shrink-0 text-slate-400 transition-transform group-open:rotate-90" /><StatusIcon status={group.status} /><span className="min-w-0 flex-1"><span className="block text-xs font-bold text-slate-700">{group.step}</span><span className="mt-1 block text-[10px] text-slate-400">{group.tasks.length ? group.tasks.length + " 个分析任务" : group.nodes.length + " 个 Agent 节点"} · 最新：{displayLabel(group.latest)}</span></span><span className="shrink-0 text-[10px] text-slate-400">{group.status}</span></summary><div className="space-y-3 border-t border-slate-100 px-3 pb-3 pt-2">{group.tasks.length ? group.tasks.map((task) => <TaskCard key={task.taskId} group={task} />) : group.nodes.length ? group.nodes.map((node) => <Drawer key={node.node} title={"节点：" + node.node} subtitle={node.events.length + " 个返回项"} status={node.status} trigger={(open) => <button type="button" onClick={open} className="group w-full rounded-md border border-blue-100 bg-blue-50/30 px-3 py-2.5 text-left hover:border-blue-300"><span className="flex items-start gap-2"><ChevronRight className="mt-0.5 size-3.5 shrink-0 text-slate-400" /><StatusIcon status={node.status} /><span className="min-w-0 flex-1"><span className="block break-all text-[11px] font-bold text-slate-700">节点：{node.node}</span><span className="mt-0.5 block text-[10px] text-slate-400">{node.events.length} 个返回项 · 查看详情</span></span></span></button>}>{node.events.map((item) => <EventDetails key={item.key} event={item.event} title={item.label} status={item.status} />)}</Drawer>) : group.events.map((event) => <EventDetails key={event.key} event={event} status={itemStatus(event, group.events)} />)}</div></details>;
}

function RoundCard({ group }: { group: RoundGroup }) {
  return <details open={group.status === "running" || group.status === "failed"} className="group rounded-xl border border-slate-200 bg-slate-50/60"><summary className="flex cursor-pointer list-none items-start gap-2 px-3 py-3"><ChevronRight className="mt-0.5 size-3.5 shrink-0 text-slate-400 transition-transform group-open:rotate-90" /><StatusIcon status={group.status} /><span className="min-w-0 flex-1"><span className="block text-xs font-extrabold text-slate-700">{group.label}</span>{group.summary && <span className="mt-1 block text-[10px] text-slate-400">{group.summary}</span>}</span><span className="shrink-0 text-[10px] text-slate-400">{group.status}</span></summary><div className="space-y-2 border-t border-slate-100 px-2.5 pb-3 pt-2.5">{group.steps.map((step) => <StepCard key={step.step} group={step} />)}</div></details>;
}

interface ProgressPhase {
  label: string;
  status: RunStatus;
  durationMs?: number;
}

interface ProgressMilestone {
  key: string;
  label: string;
  status: RunStatus;
  detail?: string;
  durationMs?: number;
  phases?: ProgressPhase[];
}

/** 进度里程碑：按时间拍平的叙事（理解 → 划分 → 任务/查询 → 报告）；轮次与生命周期样板在步骤返回 tab 查看。 */
function deriveProgressMilestones(ordered: DebugEvent[], tasks: TaskSummary[]): ProgressMilestone[] {
  const firstMs = (predicate: (event: DebugEvent) => boolean) => {
    const event = ordered.find(predicate);
    return event ? eventTimestampMs(event) : NaN;
  };
  const milestones: ProgressMilestone[] = [];

  const contextStart = firstMs((event) => event.type.startsWith("context.") || customEventOf(event, "question_route"));
  if (Number.isFinite(contextStart)) {
    const boundary = ordered.find((event) => event.type === "context.completed" || event.type === "context.failed");
    const routeEvent = ordered.find((event) => customEventOf(event, "question_route"));
    const planEvent = ordered.find((event) => event.type === "context.plan");
    const details: string[] = [];
    if (routeEvent) {
      const mode = typeof routeEvent.payload.execution_mode === "string" ? routeEvent.payload.execution_mode : "";
      if (mode) details.push("路由：" + (ROUTE_MODE_LABELS[mode] ?? mode));
    }
    if (planEvent) {
      const candidateCount = typeof planEvent.payload.candidate_count === "number" ? planEvent.payload.candidate_count : null;
      const selectedCount = typeof planEvent.payload.selected_count === "number" ? planEvent.payload.selected_count : null;
      const tokenCount = typeof planEvent.payload.token_count === "number" ? planEvent.payload.token_count : null;
      if (candidateCount !== null && selectedCount !== null) details.push(`召回 ${selectedCount}/${candidateCount} 项`);
      if (tokenCount !== null && tokenCount > 0) details.push(formatTokens(tokenCount));
    }
    milestones.push({
      key: "understand",
      label: "理解问题并召回上下文",
      status: boundary?.type === "context.failed" ? "failed" : boundary ? "success" : "running",
      detail: details.length ? details.join(" · ") : undefined,
      durationMs: boundary ? eventTimestampMs(boundary) - contextStart : undefined,
    });
  }

  const planStart = firstMs((event) => event.type.startsWith("planner.") || customEventOf(event, "analysis_plan"));
  if (Number.isFinite(planStart)) {
    const boundary = ordered.find((event) =>
      event.type === "planner.completed"
      || event.type === "planner.failed"
      || (customEventOf(event, "analysis_plan") && (event.status === "success" || event.status === "failed")));
    const planFailed = Boolean(boundary && (boundary.type === "planner.failed" || boundary.status === "failed"));
    const planErrorDetail = planFailed && boundary
      ? [boundary.payload.error_message, boundary.payload.error].find(
          (value): value is string => typeof value === "string" && value !== "",
        )
      : undefined;
    milestones.push({
      key: "plan",
      label: "划分分析任务",
      status: planFailed ? "failed" : boundary ? "success" : "running",
      detail: planFailed ? planErrorDetail : tasks.length ? `${tasks.length} 个任务` : undefined,
      durationMs: boundary ? eventTimestampMs(boundary) - planStart : undefined,
    });
  }

  if (tasks.length) {
    tasks.forEach((task, index) => {
      const taskEvents = task.events.slice().sort((left, right) => eventOrder(left) - eventOrder(right));
      // phase 链：analysis_task_phase 依次推进，以下一个 phase / 依赖解析 / 任务结果作为当前段收口。
      const phases: ProgressPhase[] = [];
      taskEvents.forEach((event, eventIndex) => {
        if (!customEventOf(event, "analysis_task_phase")) return;
        const next = taskEvents.slice(eventIndex + 1).find((item) =>
          customEventOf(item, "analysis_task_phase")
          || customEventOf(item, "analysis_task_resolved")
          || customEventOf(item, "analysis_task_result"));
        phases.push({
          label: typeof event.payload.phase === "string" ? event.payload.phase : "执行阶段",
          status: "success",
          durationMs: next ? eventTimestampMs(next) - eventTimestampMs(event) : undefined,
        });
      });
      if (phases.length) {
        const lastPhase = phases[phases.length - 1];
        lastPhase.status = !terminal(task.status) ? "running" : task.status === "failed" ? "failed" : "success";
      }
      const result = taskEvents.find((event) => customEventOf(event, "analysis_task_result"));
      const start = taskEvents.length ? eventTimestampMs(taskEvents[0]) : NaN;
      milestones.push({
        key: "task-" + task.task_id,
        label: `任务 ${index + 1} · ${task.question || task.task_id}`,
        status: task.status,
        durationMs: result ? eventTimestampMs(result) - start : undefined,
        phases,
      });
    });
  } else if (ordered.some((event) => event.type === "action.committed" || event.type.startsWith("tool."))) {
    // 无任务清单时按工具执行聚合；多轮循环以最后一次终态收口，后续新的 started 重新打开。
    let status: RunStatus = "running";
    let endMs = NaN;
    for (const event of ordered) {
      if (event.type === "tool.completed" || event.type === "tool.failed") {
        endMs = eventTimestampMs(event);
        status = event.type === "tool.failed" ? "failed" : "success";
      } else if ((event.type === "tool.started" || event.type === "action.committed") && Number.isFinite(endMs)) {
        status = "running";
        endMs = NaN;
      }
    }
    const start = firstMs((event) => event.type === "action.committed" || event.type.startsWith("tool."));
    milestones.push({
      key: "execute",
      label: "执行数据查询",
      status,
      durationMs: Number.isFinite(endMs) ? endMs - start : undefined,
    });
  }

  const runCompleted = ordered.some((event) => event.type === "run.completed");
  const reportStart = firstMs((event) => customEventOf(event, "report_plan_result") || customEventOf(event, "rendered_report"));
  if (Number.isFinite(reportStart)) {
    const boundary = ordered.find((event) => customEventOf(event, "rendered_report") || event.type === "run.completed");
    milestones.push({
      key: "report",
      label: "汇总生成报告",
      status: boundary ? "success" : "running",
      durationMs: boundary ? eventTimestampMs(boundary) - reportStart : undefined,
    });
  } else if (tasks.length && !runCompleted) {
    // 任务已规划、报告阶段未开始：以待办里程碑回答「还剩什么」。
    milestones.push({ key: "report", label: "汇总生成报告", status: "pending" });
  }

  if (runFailed(ordered)) {
    for (let index = milestones.length - 1; index >= 0; index -= 1) {
      if (milestones[index].status === "running") {
        milestones[index].status = "failed";
        break;
      }
    }
  }
  return milestones;
}

function deriveProgressHeadline(
  ordered: DebugEvent[],
  tasks: TaskSummary[],
  milestones: ProgressMilestone[],
): { text: string; tone: "running" | "success" | "failed" | "waiting" } {
  if (ordered.some((event) => event.type === "confirmation.required")
    && !ordered.some((event) => event.type === "confirmation.resolved")) {
    return { text: "等待你确认", tone: "waiting" };
  }
  if (runFailed(ordered)) return { text: "执行失败", tone: "failed" };
  if (ordered.some((event) => event.type === "run.completed")) return { text: "已完成", tone: "success" };
  if (tasks.length) {
    const runningIndex = tasks.findIndex((task) => task.status === "running");
    const index = runningIndex >= 0 ? runningIndex : 0;
    const phaseEvent = tasks[index].events.filter((event) => customEventOf(event, "analysis_task_phase")).at(-1);
    const phaseLabel = runningIndex >= 0 && phaseEvent && typeof phaseEvent.payload.phase === "string"
      ? ` · ${phaseEvent.payload.phase}`
      : "";
    return { text: `正在执行 任务 ${index + 1}/${tasks.length}${phaseLabel}`, tone: "running" };
  }
  const running = milestones.find((milestone) => milestone.status === "running");
  if (running) {
    const text = running.key === "understand" ? "正在理解问题"
      : running.key === "plan" ? "正在划分分析任务"
      : running.key === "execute" ? "正在执行数据查询"
      : running.key === "report" ? "正在汇总生成报告"
      : "正在执行";
    return { text, tone: "running" };
  }
  return { text: "正在准备执行", tone: "running" };
}

function ProgressMilestoneRow({ milestone }: { milestone: ProgressMilestone }) {
  return (
    <div className="flex items-start gap-2.5">
      <span className="mt-0.5 shrink-0"><StatusIcon status={milestone.status} /></span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline justify-between gap-2">
          <span className={"truncate text-xs font-bold " + (milestone.status === "running" ? "text-blue-700" : "text-slate-700")}>
            {milestone.label}
          </span>
          {typeof milestone.durationMs === "number" && milestone.durationMs > 0 && (
            <span className="shrink-0 text-[10px] text-slate-400">{formatDuration(milestone.durationMs)}</span>
          )}
        </div>
        {milestone.detail && (
          <div className={"mt-1 break-words text-[10px] leading-4 " + (milestone.status === "failed" ? "text-rose-600" : "text-slate-400")}>
            {milestone.detail}
          </div>
        )}
        {milestone.phases && milestone.phases.length > 0 && (
          <div className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[10px] leading-4">
            {milestone.phases.map((phase, index) => (
              <span key={milestone.key + "-phase-" + index} className="flex items-center gap-1.5">
                {index > 0 && <span className="text-slate-300">→</span>}
                <span className={
                  phase.status === "running" ? "font-bold text-blue-600"
                    : phase.status === "failed" ? "text-rose-500"
                    : "text-slate-400"
                }>
                  {phase.label}
                  {typeof phase.durationMs === "number" && phase.durationMs > 0 ? ` ${formatDuration(phase.durationMs)}` : ""}
                </span>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function ProgressView({ events, tasks }: { events: DebugEvent[]; tasks: TaskSummary[] }) {
  const ordered = events.slice().sort((left, right) => eventOrder(left) - eventOrder(right));
  const runCompleted = ordered.some((event) => event.type === "run.completed");
  const interrupted = runFailed(ordered);
  const waitingConfirmation = ordered.some((event) => event.type === "confirmation.required")
    && !ordered.some((event) => event.type === "confirmation.resolved");
  const live = !runCompleted && !interrupted && !waitingConfirmation;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!live) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [live]);

  const milestones = deriveProgressMilestones(ordered, tasks);
  const headline = deriveProgressHeadline(ordered, tasks, milestones);
  const startMs = ordered.length ? eventTimestampMs(ordered[0]) : NaN;
  const terminalEvents = ordered.filter((event) =>
    event.type === "run.completed"
    || event.type === "run.failed"
    || event.type === "run.timeout"
    || event.type === "run.cancelled"
    || event.type === "confirmation.required"
    || event.type === "stream.failed");
  const endMs = terminalEvents.length ? eventTimestampMs(terminalEvents[terminalEvents.length - 1]) : now;
  const elapsedMs = Number.isFinite(startMs) ? Math.max(0, (live ? now : endMs) - startMs) : 0;
  const done = tasks.filter((task) => terminal(task.status)).length;
  const percent = tasks.length ? Math.round((done / tasks.length) * 100) : runCompleted ? 100 : null;
  const barTone = interrupted ? "bg-rose-500" : waitingConfirmation ? "bg-amber-400" : "bg-blue-500";
  const headlineIcon = headline.tone === "running"
    ? <Loader2 className="size-4 shrink-0 animate-spin text-blue-600" />
    : headline.tone === "failed"
      ? <XCircle className="size-4 shrink-0 text-rose-500" />
      : headline.tone === "waiting"
        ? <AlertCircle className="size-4 shrink-0 text-amber-500" />
        : <CheckCircle2 className="size-4 shrink-0 text-emerald-500" />;
  const headlineColor = headline.tone === "running" ? "text-blue-700"
    : headline.tone === "failed" ? "text-rose-600"
    : headline.tone === "waiting" ? "text-amber-600"
    : "text-emerald-600";
  return (
    <div>
      <div className="flex items-center gap-2">
        {headlineIcon}
        <span className={"text-sm font-extrabold " + headlineColor}>{headline.text}</span>
      </div>
      <div className="mt-2.5 flex items-center gap-2.5">
        <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-slate-100">
          {percent === null
            ? <div className={"h-full w-full animate-pulse rounded-full opacity-40 " + barTone} />
            : <div className={"h-full rounded-full transition-all " + barTone} style={{ width: percent + "%" }} />}
        </div>
        <span className="shrink-0 text-[10px] font-bold text-slate-400">
          {percent === null ? "执行中" : `${percent}%`} · 已执行 {formatDuration(elapsedMs) || "0 秒"}
        </span>
      </div>
      <div className="mt-4 space-y-3 border-t border-slate-100 pt-3.5">
        {milestones.map((milestone) => <ProgressMilestoneRow key={milestone.key} milestone={milestone} />)}
      </div>
      {!milestones.length && <div className="py-10 text-center text-xs text-slate-400">提交问题后显示执行进度</div>}
    </div>
  );
}

function AllEvents({ events }: { events: DebugEvent[] }) {
  if (!events.length) return <div className="py-10 text-center text-xs text-slate-400">收到 SSE 事件后，这里会显示全部 JSON</div>;
  return <div className="space-y-2">{events.map((event) => <EventDetails key={event.key} event={event} status={itemStatus(event, events)} />)}</div>;
}

export function ExecutionPanel({
  running,
  debugEvents,
  loading = false,
  error = "",
}: {
  running: boolean;
  debugEvents: DebugEvent[];
  loading?: boolean;
  error?: string;
}) {
  const rawEvents = debugEvents
    .slice()
    .sort((left, right) => eventOrder(left) - eventOrder(right));
  const orderedEvents = rawEvents
    .slice()
    .sort((left, right) => orderOf(left.step) - orderOf(right.step) || eventOrder(left) - eventOrder(right));
  const tasks = buildTasks(rawEvents);
  const rounds = buildRoundGroups(rawEvents, tasks);
  const stepCount = rounds.reduce((sum, round) => sum + round.steps.length, 0);
  const compactedEvents = compactAllEvents(rawEvents);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [tab, setTab] = useState("progress");
  const handleTab = (value: string | null) => {
    if (!value) return;
    setTab(value);
    scrollRef.current?.scrollTo({ top: 0, behavior: "auto" });
    requestAnimationFrame(() => scrollRef.current?.scrollTo({ top: 0, behavior: "auto" }));
  };
  return <aside className="flex min-h-0 min-w-0 basis-[430px] shrink-0 flex-[0_0_430px] flex-col border-l border-slate-200 bg-white 2xl:basis-[500px] 2xl:flex-[0_0_500px]"><div className="border-b border-slate-200 px-5 py-4"><div className="flex items-center gap-2 text-sm font-extrabold text-slate-800"><ClipboardList className="size-4 text-blue-600" />执行过程</div><p className="mt-1 text-[11px] text-slate-400">实时展示本次运行的总体进度、任务返回和节点事件</p></div><Tabs value={tab} onValueChange={handleTab} className="min-h-0 flex-1 gap-0"><TabsList variant="line" className="sticky top-0 z-20 grid h-12 w-full shrink-0 grid-cols-3 justify-stretch overflow-x-auto rounded-none border-b border-slate-200 bg-white px-5 py-0 shadow-[0_4px_10px_-8px_rgba(15,23,42,0.35)]"><TabsTrigger value="progress" className="gap-1 text-[11px]"><ClipboardList className="size-3.5" />进度</TabsTrigger><TabsTrigger value="returns" className="gap-1 text-[11px]"><Braces className="size-3.5" />步骤返回{stepCount ? " " + stepCount : ""}</TabsTrigger><TabsTrigger value="events" className="gap-1 text-[11px]"><Braces className="size-3.5" />工具与事件{compactedEvents.length ? " " + compactedEvents.length : ""}</TabsTrigger></TabsList>{loading && <div className="flex items-center gap-2 border-b border-blue-100 bg-blue-50 px-5 py-2.5 text-[11px] font-semibold text-blue-700"><Loader2 className="size-3.5 animate-spin" />正在加载该轮次的执行过程...</div>}{error && <div className="border-b border-amber-100 bg-amber-50 px-5 py-2.5 text-[11px] leading-5 text-amber-800">{error}</div>}<div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain"><TabsContent value="progress" className="mt-4 px-5 pb-5"><ProgressView events={orderedEvents} tasks={tasks} /></TabsContent><TabsContent value="returns" className="mt-4 space-y-2 px-5 pb-5">{rounds.length ? rounds.length > 1 ? rounds.map((round) => <RoundCard key={round.round} group={round} />) : rounds[0].steps.map((group) => <StepCard key={group.step} group={group} />) : <div className="py-10 text-center text-xs text-slate-400">收到节点返回后，这里会显示主步骤摘要</div>}</TabsContent><TabsContent value="events" className="mt-4 px-5 pb-5"><AllEvents events={compactedEvents} /></TabsContent></div></Tabs><div className="border-t border-slate-200 px-5 py-3"><div className="flex items-center gap-2 text-[11px] font-semibold text-slate-400"><Timer className="size-3.5" />{loading ? "正在加载执行过程" : running ? "正在执行" : rawEvents.length ? "本次执行已结束" : "等待提问"}</div></div></aside>;
}
