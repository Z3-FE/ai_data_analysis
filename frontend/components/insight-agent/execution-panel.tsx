"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  AlertCircle,
  Braces,
  CheckCircle2,
  ChevronRight,
  CircleHelp,
  Circle,
  ClipboardList,
  Layers3,
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

type RunStatus = "pending" | "running" | "success" | "partial" | "failed";

export interface DebugEvent {
  key: string;
  sequence: number;
  type: string;
  step: string;
  sourceStep: string;
  node: string;
  taskId?: string;
  status?: RunStatus;
  receivedAt: string;
  payload: Record<string, unknown>;
  truncatedFields?: string[];
}

interface DisplayEvent {
  key: string;
  label: string;
  event: DebugEvent;
}

interface NodeGroup {
  node: string;
  status: RunStatus;
  latest: DebugEvent;
  events: DisplayEvent[];
}

interface TaskSummary {
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
  "开始执行", "判断问题路由", "生成分析计划", "提取关键词",
  "召回字段", "召回表", "召回指标", "召回维度值", "合并召回信息", "过滤指标",
  "过滤表", "整理 SQL 上下文", "生成 SQL", "执行 SQL", "增强查询结果",
  "执行分析任务", "汇总全部分析证据", "生成报告规划", "渲染最终报告",
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

function mainStep(sourceStep: string, type: string) {
  if (type.startsWith("run.") || type === "stream.failed") return "开始执行";
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
  const node = typeof event.node === "string" && event.node.trim() ? event.node.trim() : "unknown_node";
  const phase = typeof event.phase === "string" ? event.phase : "";
  const truncated: string[] = [];
  const payload = safePayload(event, truncated) as Record<string, unknown>;
  if (truncated.length) {
    payload.debug_truncated = true;
    payload.debug_truncated_fields = truncated;
  }
  return {
    key: type + ":" + sourceStep + ":" + node + ":" + phase + ":" + (taskId || ""),
    sequence: -1,
    type,
    step: mainStep(sourceStep, type),
    sourceStep,
    node,
    taskId,
    status: statusOf(event.status),
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

function statusForEvents(events: DebugEvent[], fallback?: RunStatus): RunStatus {
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
    } else if (RESULT_EVENT_TYPES.has(event.type)) {
      latestStatus = "success";
    }
  }
  return latestStatus || "pending";
}

function taskStatus(events: DebugEvent[]): RunStatus {
  const result = events.filter((event) => event.type === "analysis_task_result").at(-1);
  if (result && result.status === "failed") return "failed";
  if (result && result.status === "partial") return "partial";
  if (result) return "success";
  if (events.some((event) => event.type === "error")) return "failed";
  return events.length ? "running" : "pending";
}

function eventScope(event: DebugEvent) {
  const phase = typeof event.payload.phase === "string"
    ? event.payload.phase
    : event.type === "analysis_task_resolved"
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
      .filter((event) => event.type === "analysis_task_result")
      .map(eventBaseScope),
  );
  const terminalScopes = new Set(
    events
      .filter((event) =>
        event.type === "error" ||
        terminal(event.status) ||
        event.type === "analysis_task_resolved",
      )
      .map(eventScope),
  );
  const latestRunning = new Map<string, DebugEvent>();
  for (const event of events) {
    if (event.status === "running" && !terminalTaskScopes.has(eventBaseScope(event)) && !terminalScopes.has(eventScope(event))) {
      // 同一分析任务的不同 phase 是一条执行链，只保留最新的运行阶段。
      const runningKey = event.type === "analysis_task_phase" ? eventBaseScope(event) : eventScope(event);
      latestRunning.set(runningKey, event);
    }
  }
  return events.filter((event) => {
    if (event.status !== "running") return true;
    const baseScope = eventBaseScope(event);
    const scope = eventScope(event);
    const runningKey = event.type === "analysis_task_phase" ? baseScope : scope;
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
  if (event.type === "context.completed") return "上下文构建完成";
  if (event.type === "planner.started") return "开始规划下一步";
  if (event.type === "planner.completed") return "规划完成";
  if (event.type === "planner.retrying") return "重新规划";
  if (event.type === "planner.failed") return "规划失败";
  if (event.type === "action.committed") return "动作已提交";
  if (event.type === "tool.started") return "工具开始执行";
  if (event.type === "tool.progress") return "工具执行进度";
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
  const visible: DisplayEvent[] = [];
  for (const event of events) {
    if (hasNonProgress && event.type === "progress") continue;
    if (hasReasoningResult && event.type === "reasoning_chunk") continue;
    if (hasLlmResult && event.type === "llm_chunk") continue;
    // 同一个展示标题可能对应多个返回事件，不能用标题作为 Map key 覆盖前面的结果。
    visible.push({ key: event.key + ":display", label: displayLabel(event), event });
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

function compactAllEvents(events: DebugEvent[]): DebugEvent[] {
  const compacted: DebugEvent[] = [];
  const streamIndexes = new Map<string, number>();

  for (const event of events) {
    if (!STREAM_EVENT_TYPES.has(event.type)) {
      compacted.push(event);
      continue;
    }

    const scope = streamScope(event);
    const existingIndex = streamIndexes.get(scope);
    if (existingIndex === undefined) {
      const chunk = streamText(event);
      compacted.push({
        ...event,
        key: event.key + ":compact",
        payload: {
          ...event.payload,
          chunk: chunk || event.payload.chunk,
          combined_text: chunk,
          chunk_count: 1,
          first_sequence: event.sequence,
          last_sequence: event.sequence,
        },
      });
      streamIndexes.set(scope, compacted.length - 1);
      continue;
    }

    const current = compacted[existingIndex];
    const currentText = typeof current.payload.combined_text === "string" ? current.payload.combined_text : "";
    const chunk = streamText(event);
    const chunkCount = typeof current.payload.chunk_count === "number" ? current.payload.chunk_count : 1;
    compacted[existingIndex] = {
      ...current,
      sequence: event.sequence,
      receivedAt: event.receivedAt,
      status: event.status || current.status,
      payload: {
        ...current.payload,
        chunk: currentText + chunk,
        combined_text: currentText + chunk,
        chunk_count: chunkCount + 1,
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

function nodeGroups(events: DebugEvent[], fallback?: RunStatus): NodeGroup[] {
  const grouped = new Map<string, DebugEvent[]>();
  for (const event of events) grouped.set(event.node, [...(grouped.get(event.node) || []), event]);
  return Array.from(grouped.entries()).map(([node, nodeEvents]) => ({
    node,
    status: statusForEvents(nodeEvents, fallback),
    latest: nodeEvents[nodeEvents.length - 1],
    events: displayEvents(nodeEvents),
  })).sort((left, right) => eventOrder(left.latest) - eventOrder(right.latest));
}

function buildTasks(events: DebugEvent[]): TaskSummary[] {
  const summaries = new Map<string, TaskSummary>();
  for (const event of events) {
    if (event.type !== "analysis_plan" || !Array.isArray(event.payload.tasks)) continue;
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

function buildTaskGroups(tasks: TaskSummary[]): TaskGroup[] {
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
        nodes: nodeGroups(stepEvents, task.status),
        display: displayEvents(stepEvents),
      }))
      .sort((left, right) => eventOrder(left.latest) - eventOrder(right.latest));
    return { taskId: task.task_id, status: task.status, task, events, steps };
  });
}

function taskStepLabel(event: DebugEvent) {
  if (event.type === "analysis_task_phase" && typeof event.payload.phase === "string") {
    return event.payload.phase;
  }
  if (event.type === "analysis_task_result") return "分析任务结果";
  if (event.type === "analysis_task_resolved") return "依赖结果解析";
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
      status: statusForEvents(visible),
      latest: visible[visible.length - 1] || stepEvents[stepEvents.length - 1],
      events: visible,
      nodes: nodeGroups(visible),
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
      tasks: buildTaskGroups(tasks),
    });
  }
  return result.sort((left, right) => orderOf(left.step) - orderOf(right.step));
}

function taskOverview(tasks: TaskSummary[]): DebugEvent {
  const status = aggregateTaskStatus(tasks);
  return { key: "analysis-task-overview", sequence: -1, type: "analysis_task_overview", step: "执行分析任务", sourceStep: "执行分析任务", node: "execute_analysis", status, receivedAt: new Date().toISOString(), payload: { type: "analysis_task_overview", message: "分析任务已按 task_id 归类。", task_count: tasks.length } };
}

function json(value: unknown) {
  const serialized = JSON.stringify(value, null, 2);
  return serialized === undefined
    ? String(value)
    : serialized.replaceAll("\\n", "\n").replaceAll("\\r", "\r").replaceAll("\\t", "\t");
}

function StatusIcon({ status }: { status: RunStatus | undefined }) {
  if (status === "success") return <CheckCircle2 className="size-4 shrink-0 text-emerald-500" />;
  if (status === "failed") return <XCircle className="size-4 shrink-0 text-rose-500" />;
  if (status === "partial") return <AlertCircle className="size-4 shrink-0 text-amber-500" />;
  if (status === "pending") return <CircleHelp className="size-4 shrink-0 text-slate-400" />;
  if (status === "running") return <Loader2 className="size-4 shrink-0 animate-spin text-blue-500" />;
  return <Circle className="size-4 shrink-0 text-slate-300" />;
}

function EventDetails({ event, title }: { event: DebugEvent; title?: string }) {
  return <details className="group rounded-md border border-slate-200 bg-white"><summary className="flex cursor-pointer list-none items-start gap-2 px-3 py-2.5"><ChevronRight className="mt-0.5 size-3.5 shrink-0 text-slate-400 transition-transform group-open:rotate-90" /><StatusIcon status={event.status || (event.type === "error" ? "failed" : undefined)} /><span className="min-w-0 flex-1"><span className="block break-words text-[11px] font-bold text-slate-700">{title || event.step}</span><span className="mt-0.5 block break-words text-[10px] text-slate-400">{displayLabel(event)} · 节点：{event.node}{event.taskId ? " · 任务：" + event.taskId : ""}</span></span><span className="shrink-0 text-[9px] text-slate-400">{new Date(event.receivedAt).toLocaleTimeString("zh-CN")}</span></summary><div className="border-t border-slate-100 px-3 pb-3 pt-2">{event.truncatedFields?.length ? <div className="mb-2 rounded bg-amber-50 px-2 py-1.5 text-[10px] leading-4 text-amber-800">调试预览已截断：{event.truncatedFields.join("；")}</div> : null}<pre className="max-h-[360px] overflow-auto whitespace-pre-wrap break-words rounded bg-slate-950 p-3 text-[10px] leading-5 text-slate-100">{json(event.payload)}</pre></div></details>;
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
  return <><div className="space-y-2 rounded-md border border-slate-200 bg-white px-3 py-2.5 text-[11px] leading-5">{task.question && <div><b className="text-slate-400">查询问题：</b><span className="text-slate-600">{task.question}</span></div>}{task.resolved_question && task.resolved_question !== task.question && <div><b className="text-slate-400">实际查询：</b><span className="text-slate-600">{task.resolved_question}</span></div>}{task.purpose && <div><b className="text-slate-400">任务用途：</b><span className="text-slate-600">{task.purpose}</span></div>}<div><b className="text-slate-400">依赖任务：</b><span className="text-slate-600">{task.depends_on.length ? task.depends_on.join("、") : "无"}</span></div>{task.node && <div><b className="text-slate-400">当前 Agent 节点：</b><span className="font-mono text-slate-600">{task.node}</span></div>}{task.error && <div className="rounded bg-rose-50 px-2 py-1 text-rose-700">失败原因：{task.error}</div>}</div>{group.steps.length ? <div className="space-y-2">{group.steps.map((step) => <details key={step.label} className="group rounded-md border border-slate-200 bg-white"><summary className="flex cursor-pointer list-none items-start gap-2 px-3 py-2.5"><ChevronRight className="mt-0.5 size-3.5 shrink-0 text-slate-400 transition-transform group-open:rotate-90" /><StatusIcon status={step.status} /><span className="min-w-0 flex-1"><span className="block text-[11px] font-bold text-slate-700">{step.label}</span><span className="mt-0.5 block text-[10px] text-slate-400">{step.nodes.length} 个 Agent 节点 · 最新：{displayLabel(step.latest)}</span></span><span className="shrink-0 text-[10px] text-slate-400">{step.status}</span></summary><div className="space-y-2 border-t border-slate-100 px-3 pb-3 pt-2">{step.nodes.length ? step.nodes.map((node) => <details key={node.node} className="group rounded-md border border-blue-100 bg-blue-50/30"><summary className="flex cursor-pointer list-none items-start gap-2 px-3 py-2.5"><ChevronRight className="mt-0.5 size-3.5 shrink-0 text-slate-400 transition-transform group-open:rotate-90" /><StatusIcon status={node.status} /><span className="min-w-0 flex-1"><span className="block text-[11px] font-bold text-slate-700">节点：{node.node}</span><span className="mt-0.5 block text-[10px] text-slate-400">{node.events.length} 个返回项</span></span></summary><div className="space-y-2 border-t border-blue-100 px-3 pb-3 pt-2">{node.events.map((item) => <EventDetails key={item.key} event={item.event} title={item.label} />)}</div></details>) : step.display.map((item) => <EventDetails key={item.key} event={item.event} title={item.label} />)}</div></details>)}</div> : <div className="py-5 text-center text-xs text-slate-400">该任务暂无详细返回</div>}</>;
}

function StepCard({ group }: { group: StepGroup }) {
  return <details open={group.status === "failed"} className="group rounded-lg border border-slate-200 bg-white"><summary className="flex cursor-pointer list-none items-start gap-2 px-3 py-3"><ChevronRight className="mt-0.5 size-3.5 shrink-0 text-slate-400 transition-transform group-open:rotate-90" /><StatusIcon status={group.status} /><span className="min-w-0 flex-1"><span className="block text-xs font-bold text-slate-700">{group.step}</span><span className="mt-1 block text-[10px] text-slate-400">{group.tasks.length ? group.tasks.length + " 个分析任务" : group.nodes.length + " 个 Agent 节点"} · 最新：{displayLabel(group.latest)}</span></span><span className="shrink-0 text-[10px] text-slate-400">{group.status}</span></summary><div className="space-y-3 border-t border-slate-100 px-3 pb-3 pt-2">{group.tasks.length ? group.tasks.map((task) => <TaskCard key={task.taskId} group={task} />) : group.nodes.length ? group.nodes.map((node) => <Drawer title={"节点：" + node.node} subtitle={node.events.length + " 个返回项"} status={node.status} trigger={(open) => <button type="button" onClick={open} className="group w-full rounded-md border border-blue-100 bg-blue-50/30 px-3 py-2.5 text-left hover:border-blue-300"><span className="flex items-start gap-2"><ChevronRight className="mt-0.5 size-3.5 shrink-0 text-slate-400" /><StatusIcon status={node.status} /><span className="min-w-0 flex-1"><span className="block break-all text-[11px] font-bold text-slate-700">节点：{node.node}</span><span className="mt-0.5 block text-[10px] text-slate-400">{node.events.length} 个返回项 · 查看详情</span></span></span></button>}>{node.events.map((item) => <EventDetails key={item.key} event={item.event} title={item.label} />)}</Drawer>) : group.events.map((event) => <EventDetails key={event.key} event={event} />)}</div></details>;
}

function ProgressView({ events, tasks }: { events: DebugEvent[]; tasks: TaskSummary[] }) {
  const groups = new Map<string, DebugEvent[]>();
  for (const event of events.filter((item) => !STREAM_EVENT_TYPES.has(item.type) && !item.taskId)) {
    const label = event.step;
    groups.set(label, [...(groups.get(label) || []), event]);
  }
  if (tasks.length) groups.set("执行分析任务", [taskOverview(tasks)]);
  const steps = Array.from(groups.entries())
    .map(([step, values]) => ({
      step,
      status: step === "执行分析任务" ? aggregateTaskStatus(tasks) : statusForEvents(currentEvents(values)),
      latest: values[values.length - 1],
    }))
    .sort((left, right) => orderOf(left.step) - orderOf(right.step));
  const completed = tasks.filter((task) => terminal(task.status)).length;
  return <><div className="space-y-3">{steps.map((step, index) => <div key={step.step} className="relative flex gap-3">{index < steps.length - 1 && <span className="absolute left-[7px] top-5 h-[calc(100%+12px)] w-px bg-slate-200" />}<StatusIcon status={step.status} /><div className="min-w-0 pb-1"><div className="text-xs font-bold text-slate-700">{step.step}</div>{step.step === "执行分析任务" && tasks.length ? <div className="mt-1 text-[11px] text-slate-400">已完成 {completed} / {tasks.length} 个分析任务</div> : typeof step.latest.payload.message === "string" ? <div className="mt-1 break-words text-[11px] leading-5 text-slate-400">{step.latest.payload.message}</div> : null}</div></div>)}{!steps.length && <div className="py-10 text-center text-xs text-slate-400">提交问题后显示执行步骤</div>}</div>{tasks.length ? <div className="mt-4 border-t border-slate-100 pt-4"><div className="mb-2 flex items-center justify-between text-[11px] font-bold text-slate-400"><span>分析任务总进度</span><span>{completed} / {tasks.length}</span></div><div className="h-1.5 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-blue-500 transition-all" style={{ width: (completed / tasks.length * 100) + "%" }} /></div></div> : null}</>;
}

function TaskList({ tasks }: { tasks: TaskSummary[] }) {
  const groups = buildTaskGroups(tasks);
  if (!groups.length) return <div className="py-10 text-center text-xs text-slate-400">分析计划返回后，这里会按 task_id 展示任务</div>;
  return <div className="space-y-2">{groups.map((group) => <TaskCard key={group.taskId} group={group} />)}</div>;
}

function AllEvents({ events }: { events: DebugEvent[] }) {
  if (!events.length) return <div className="py-10 text-center text-xs text-slate-400">收到 SSE 事件后，这里会显示全部 JSON</div>;
  return <div className="space-y-2">{events.map((event) => <EventDetails key={event.key} event={event} />)}</div>;
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
  const steps = buildStepGroups(orderedEvents, tasks);
  const compactedEvents = compactAllEvents(rawEvents);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [tab, setTab] = useState("progress");
  const handleTab = (value: string | null) => {
    if (!value) return;
    setTab(value);
    scrollRef.current?.scrollTo({ top: 0, behavior: "auto" });
    requestAnimationFrame(() => scrollRef.current?.scrollTo({ top: 0, behavior: "auto" }));
  };
  return <aside className="flex min-h-0 min-w-0 basis-[430px] shrink-0 flex-[0_0_430px] flex-col border-l border-slate-200 bg-white 2xl:basis-[500px] 2xl:flex-[0_0_500px]"><div className="border-b border-slate-200 px-5 py-4"><div className="flex items-center gap-2 text-sm font-extrabold text-slate-800"><ClipboardList className="size-4 text-blue-600" />执行过程</div><p className="mt-1 text-[11px] text-slate-400">实时展示本次运行的总体进度、任务返回和节点事件</p></div><Tabs value={tab} onValueChange={handleTab} className="min-h-0 flex-1 gap-0"><TabsList variant="line" className="sticky top-0 z-20 grid h-12 w-full shrink-0 grid-cols-4 justify-stretch overflow-x-auto rounded-none border-b border-slate-200 bg-white px-5 py-0 shadow-[0_4px_10px_-8px_rgba(15,23,42,0.35)]"><TabsTrigger value="progress" className="gap-1 text-[11px]"><ClipboardList className="size-3.5" />进度</TabsTrigger><TabsTrigger value="returns" className="gap-1 text-[11px]"><Braces className="size-3.5" />步骤返回{steps.length ? " " + steps.length : ""}</TabsTrigger><TabsTrigger value="tasks" className="gap-1 text-[11px]"><Layers3 className="size-3.5" />分析任务{tasks.length ? " " + tasks.length : ""}</TabsTrigger><TabsTrigger value="events" className="gap-1 text-[11px]"><Braces className="size-3.5" />全部事件{compactedEvents.length ? " " + compactedEvents.length : ""}</TabsTrigger></TabsList>{loading && <div className="flex items-center gap-2 border-b border-blue-100 bg-blue-50 px-5 py-2.5 text-[11px] font-semibold text-blue-700"><Loader2 className="size-3.5 animate-spin" />正在加载该轮次的执行过程...</div>}{error && <div className="border-b border-amber-100 bg-amber-50 px-5 py-2.5 text-[11px] leading-5 text-amber-800">{error}</div>}<div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain"><TabsContent value="progress" className="mt-4 px-5 pb-5"><ProgressView events={orderedEvents} tasks={tasks} /></TabsContent><TabsContent value="returns" className="mt-4 space-y-2 px-5 pb-5">{steps.length ? steps.map((group) => <StepCard key={group.step} group={group} />) : <div className="py-10 text-center text-xs text-slate-400">收到节点返回后，这里会显示主步骤摘要</div>}</TabsContent><TabsContent value="tasks" className="mt-4 px-5 pb-5"><TaskList tasks={tasks} /></TabsContent><TabsContent value="events" className="mt-4 px-5 pb-5"><AllEvents events={compactedEvents} /></TabsContent></div></Tabs><div className="border-t border-slate-200 px-5 py-3"><div className="flex items-center gap-2 text-[11px] font-semibold text-slate-400"><Timer className="size-3.5" />{loading ? "正在加载执行过程" : running ? "正在执行" : rawEvents.length ? "本次执行已结束" : "等待提问"}</div></div></aside>;
}
