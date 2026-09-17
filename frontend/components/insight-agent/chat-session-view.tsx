"use client";

import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import {
  AssistantRuntimeProvider,
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  type AppendMessage,
  type ThreadMessage,
  type ThreadMessageLike,
  useAuiState,
  useExternalStoreRuntime,
  useMessage,
} from "@assistant-ui/react";
import {
  AlertCircle,
  Bot,
  ChevronRight,
  Loader2,
  ListTree,
  MessageSquare,
  Send,
  User,
} from "lucide-react";
import { conversationService } from "../../services/conversations";
import { harnessService, type HarnessEvent } from "../../services/harness";
import { parseBackendDate } from "../../lib/date";
import { ReportView, type RenderedReport } from "./analysis-workspace";
import {
  ExecutionPanel,
  appendExecutionEvent,
  type DebugEvent,
  type StreamEvent,
} from "./execution-panel";
import { ChatRunSummaryChip, ChatRunTimeline } from "./chat-run-progress";

interface ChatSessionViewProps {
  conversationId: string;
}

interface BackendMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  turn_id?: string;
  created_at?: string;
}

type ExecutionMode = "daily_chat" | "single_query" | "analysis" | "clarification" | "harness";

interface BackendTurn {
  turn_id: string;
  run_id?: string;
  execution_mode?: ExecutionMode;
  status?: string;
  started_at?: string;
  completed_at?: string;
}

interface BackendOutput {
  turn_id: string;
  output_type: string;
  payload?: Record<string, unknown>;
}

interface QueryResultPayload {
  columns?: Array<string | { result_name?: string; display_name?: string }>;
  rows?: Array<Record<string, unknown>>;
  row_count?: number;
  truncated?: boolean;
}

interface ConversationHistoryData {
  conversation?: Record<string, unknown>;
  messages?: BackendMessage[];
  turns?: BackendTurn[];
  outputs?: BackendOutput[];
}

interface PendingHarnessConfirmation {
  run_id: string;
  confirmation_id: string;
  question: string;
  reason_code?: string;
  required_fields?: string[];
  expires_at?: string;
  assistant_message_id: string;
}

interface ConversationTurnMeta {
  turn_id?: string;
  execution_mode?: ExecutionMode;
  response_type: "chat" | "simple_data" | "analysis" | "clarification" | "failure";
  assistant_text: string;
  output_type?: string;
  status?: string;
  elapsed_seconds: number;
  rendered_report?: RenderedReport;
  query_result?: QueryResultPayload;
}

interface AssistantChatRuntimeProps {
  messages: ThreadMessageLike[];
  isRunning: boolean;
  children: React.ReactNode;
  onNew: (message: AppendMessage) => Promise<void>;
  onCancel: () => Promise<void>;
}

interface ExecutionProcessContextValue {
  open: boolean;
  activeTurnId?: string;
  toggle: (turnId?: string) => void;
}

const ExecutionProcessContext = createContext<ExecutionProcessContextValue | null>(null);

/** 本轮 SSE 事件缓冲，供聊天气泡内的运行进度展示与执行面板共用。 */
const RunProgressContext = createContext<DebugEvent[]>([]);

function formatTime(value?: Date | string) {
  /** 格式化消息时间，用于聊天气泡下方展示。 */

  const date = parseBackendDate(value);
  if (!date) return "--";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function getMessageText(message: ThreadMessage | ThreadMessageLike) {
  /** 从 assistant-ui 消息结构中提取纯文本内容。 */

  if (typeof message.content === "string") return message.content;

  return message.content
    .map((part) => {
      if ("text" in part && typeof part.text === "string") return part.text;
      return "";
    })
    .join("");
}

function conversationMeta(message: ThreadMessage | ThreadMessageLike) {
  /** 读取会话历史中挂载的富内容输出元数据。 */

  const value = message.metadata?.custom?.conversation;
  if (!value || typeof value !== "object") return undefined;
  return value as ConversationTurnMeta;
}

function outputResponseType(outputType?: string): ConversationTurnMeta["response_type"] {
  /** 将后端输出类型映射为聊天消息的富内容类型。 */

  if (outputType === "rendered_report") return "analysis";
  if (outputType === "query_result") return "simple_data";
  if (outputType === "clarification") return "clarification";
  if (outputType === "failure") return "failure";
  return "chat";
}

function asExecutionMode(value: unknown): ExecutionMode | undefined {
  /** 只接受后端路由协议定义的执行模式。 */

  return value === "daily_chat"
    || value === "single_query"
    || value === "analysis"
    || value === "clarification"
    ? value
    : undefined;
}

function shouldShowExecutionPanel(mode: ExecutionMode | null): boolean {
  /** 只有需要查询或分析的轮次才展示执行过程。 */

  return mode === "single_query" || mode === "analysis";
}

function buildConversationMeta(
  content: string,
  outputType?: string,
  payload: Record<string, unknown> = {},
  turn?: Partial<BackendTurn>,
  elapsedSecondsOverride?: number,
): ConversationTurnMeta | undefined {
  /** 让实时消息和历史消息使用同一份富内容元数据结构。 */

  if (!outputType) return undefined;
  const isDailyChat = turn?.execution_mode === "daily_chat";
  const startedAt = parseBackendDate(turn?.started_at);
  const completedAt = parseBackendDate(turn?.completed_at);
  const elapsedSeconds = typeof elapsedSecondsOverride === "number"
    ? elapsedSecondsOverride
    : startedAt && completedAt
    ? Math.max(0, (completedAt.getTime() - startedAt.getTime()) / 1000)
    : 0;
  return {
    turn_id: turn?.turn_id,
    execution_mode: turn?.execution_mode,
    response_type: isDailyChat ? "chat" : outputResponseType(outputType),
    assistant_text: content,
    output_type: outputType,
    status: turn?.status,
    elapsed_seconds: elapsedSeconds,
    rendered_report: !isDailyChat && outputType === "rendered_report" && isRenderedReportPayload(payload)
      ? payload as unknown as RenderedReport
      : undefined,
    query_result: !isDailyChat && outputType === "query_result"
      ? payload as QueryResultPayload
      : undefined,
  };
}

function asRecord(value: unknown): Record<string, unknown> {
  /** 只把事件中的 JSON 对象作为富内容载荷交给渲染器。 */

  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function isRenderedReportPayload(payload: Record<string, unknown>): boolean {
  /** 只有真正携带 sections 的载荷才进入报告渲染器；实时终态事件只有文字占位。 */

  return Array.isArray(payload.sections);
}

function formatQueryCell(value: unknown) {
  /** 将简单问数结果中的单元格转换成可读文本，不改变后端原始值。 */

  if (value === null || value === undefined || value === "") return "--";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function QueryResultView({ result }: { result: QueryResultPayload }) {
  /** 恢复渲染历史中的轻量问数结果，完整结果仍由后端查询链路负责。 */

  const columns = result.columns ?? [];
  const rows = result.rows ?? [];
  const columnDefs = columns.length
    ? columns.map((column, index) => {
        if (typeof column === "string") return { key: column, label: column };
        const fallback = "字段_" + String(index + 1);
        return {
          key: column.result_name ?? column.display_name ?? fallback,
          label: column.display_name ?? column.result_name ?? fallback,
        };
      })
    : Object.keys(rows[0] ?? {}).map((column) => ({ key: column, label: column }));

  if (!rows.length) {
    return (
      <div className="mt-4 border-l-2 border-blue-200 pl-4 text-sm text-slate-500">
        查询完成，未返回数据行。
      </div>
    );
  }

  return (
    <div className="mt-5 overflow-hidden rounded-xl border border-slate-200 bg-white">
      <div className="flex items-center justify-between gap-3 border-b border-slate-100 px-4 py-3">
        <span className="text-xs font-bold text-slate-700">查询结果</span>
        <span className="text-[11px] text-slate-400">
          展示 {rows.length} / {result.row_count ?? rows.length} 行
          {result.truncated ? " · 已截取预览" : ""}
        </span>
      </div>
      <div className="max-h-[360px] overflow-auto">
        <table className="min-w-full text-left text-xs">
          <thead className="sticky top-0 z-10 bg-slate-50 text-[11px] font-bold text-slate-500">
            <tr>
              {columnDefs.map((column) => (
                <th key={column.key} className="whitespace-nowrap border-b border-slate-100 px-4 py-2.5">
                  {column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {rows.map((row, rowIndex) => (
              <tr key={rowIndex} className="hover:bg-slate-50">
                {columnDefs.map((column) => (
                  <td key={column.key} className="whitespace-nowrap px-4 py-2.5 font-mono text-slate-600">
                    {formatQueryCell(row[column.key])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function getDisplayMessageText(message: ThreadMessage | ThreadMessageLike) {
  /** 生成最终展示文本，并去掉流式结束后可能残留的独立圆点。 */

  // Some streaming renderers/providers append a standalone bullet as a pending marker.
  // Only remove an isolated trailing bullet so normal markdown/list content is preserved.
  return getMessageText(message).replace(/\s+[●•]\s*$/, "");
}

function toAssistantMessage(
  message: BackendMessage,
  output?: BackendOutput,
  turn?: BackendTurn,
): ThreadMessageLike {
  /** 把消息和同一轮的结构化输出合并为可恢复的 assistant-ui 消息。 */
  const payload = output?.payload ?? {};
  const conversation = output && message.role === "assistant"
    ? buildConversationMeta(message.content, output.output_type, payload, turn)
    : undefined;

  return {
    id: message.id,
    role: message.role,
    content: [{ type: "text", text: message.content }],
    createdAt: parseBackendDate(message.created_at) ?? new Date(),
    ...(conversation ? { metadata: { custom: { conversation } } } : {}),
  };
}

function outputsByTurn(history: ConversationHistoryData) {
  /** 按轮次和输出类型保留历史输出，执行过程不能覆盖报告输出。 */

  const grouped = new Map<string, Map<string, BackendOutput>>();
  for (const output of history.outputs ?? []) {
    const byType = grouped.get(output.turn_id) ?? new Map<string, BackendOutput>();
    byType.set(output.output_type, output);
    grouped.set(output.turn_id, byType);
  }
  return grouped;
}

function displayOutputForMessage(
  outputs: Map<string, BackendOutput> | undefined,
): BackendOutput | undefined {
  /** 助手消息只绑定报告或查询等主输出，执行轨迹由按钮按需读取。 */

  if (!outputs) return undefined;
  for (const type of ["rendered_report", "query_result", "clarification", "failure", "text"]) {
    const output = outputs.get(type);
    if (output) return output;
  }
  return undefined;
}

function notifyConversationsChanged() {
  /** 通知侧边栏重新拉取会话列表，保持标题和更新时间同步。 */

  window.dispatchEvent(new CustomEvent("insight:conversations:changed"));
}

function sleep(ms: number) {
  /** 等待指定毫秒数，用于控制前端打字机渲染节奏。 */

  return new Promise((resolve) => window.setTimeout(resolve, ms));
}


interface HarnessRunRefPayload {
  user_id?: string;
  conversation_id?: string;
  thread_id?: string;
  turn_id?: string;
  run_id?: string;
}

interface HarnessStreamEvent extends StreamEvent {
  type: string;
  event_type: string;
  payload: Record<string, unknown>;
  run_ref?: HarnessRunRefPayload;
  phase?: string;
  iteration?: number;
  action_id?: string;
  timestamp?: string;
  [key: string]: unknown;
}

function harnessStep(eventType: string, source: string, payload: Record<string, unknown>) {
  if (typeof payload.step === "string" && payload.step.trim()) return payload.step;
  if (eventType.startsWith("context.")) return "构建上下文";
  if (eventType.startsWith("planner.")) return "任务划分";
  if (eventType.startsWith("tool.")) return source ? `执行工具：${source}` : "执行工具";
  if (eventType.startsWith("confirmation.")) return "等待用户确认";
  if (eventType === "action.committed") return "提交动作";
  if (eventType === "run.result") return "处理运行结果";
  return "Harness 运行";
}

function toHarnessStreamEvent(event: HarnessEvent): HarnessStreamEvent {
  const payload = event.payload ?? {};
  const eventType = event.event_type ?? event.type;
  const source = typeof event.source === "string" ? event.source : "harness";
  return {
    ...payload,
    ...event,
    payload,
    step: harnessStep(eventType, source, payload),
    node: typeof payload.node === "string" ? payload.node : source,
    type: eventType,
    event_type: eventType,
  };
}

function toHistoricalStreamEvent(value: unknown): StreamEvent | undefined {
  const raw = asRecord(value);
  const payload = asRecord(raw.payload);
  const type = typeof raw.type === "string"
    ? raw.type
    : typeof raw.event_type === "string"
      ? raw.event_type
      : undefined;
  if (!type) return undefined;
  const source = typeof raw.source === "string" ? raw.source : "harness";
  return {
    ...raw,
    ...payload,
    type,
    step: typeof raw.step === "string"
      ? raw.step
      : harnessStep(type, source, payload),
    node: typeof raw.node === "string"
      ? raw.node
      : typeof payload.node === "string"
        ? payload.node
        : source,
  };
}

function finalAnswerForHarnessEvent(event: HarnessStreamEvent) {
  const value = event.final_answer;
  return typeof value === "string" && value.trim() ? value : undefined;
}

function confirmationFromHarnessEvent(
  event: HarnessStreamEvent,
  assistantMessageId: string,
): PendingHarnessConfirmation | undefined {
  const source = event.type === "run.result"
    ? asRecord(event.loop_result)
    : event;
  const confirmation = asRecord(source.confirmation);
  const confirmationId = typeof confirmation.confirmation_id === "string"
    ? confirmation.confirmation_id
    : typeof event.confirmation_id === "string"
      ? event.confirmation_id
      : undefined;
  const question = typeof confirmation.question === "string"
    ? confirmation.question
    : typeof event.question === "string"
      ? event.question
      : undefined;
  const runId = event.run_ref?.run_id;
  if (!confirmationId || !question || !runId) return undefined;
  return {
    run_id: runId,
    confirmation_id: confirmationId,
    question,
    reason_code: typeof confirmation.reason_code === "string"
      ? confirmation.reason_code
      : typeof event.reason_code === "string"
        ? event.reason_code
        : undefined,
    required_fields: Array.isArray(confirmation.required_fields)
      ? confirmation.required_fields.filter((value): value is string => typeof value === "string")
      : Array.isArray(event.required_fields)
        ? event.required_fields.filter((value): value is string => typeof value === "string")
        : undefined,
    expires_at: typeof confirmation.expires_at === "string"
      ? confirmation.expires_at
      : typeof event.expires_at === "string"
        ? event.expires_at
        : undefined,
    assistant_message_id: assistantMessageId,
  };
}

function confirmationFromHarnessStatus(
  status: Record<string, unknown>,
  runId: string,
  assistantMessageId: string,
): PendingHarnessConfirmation | undefined {
  const pending = asRecord(status.pending_confirmation);
  if (typeof pending.confirmation_id !== "string" || typeof pending.question !== "string") {
    return undefined;
  }
  return {
    run_id: runId,
    confirmation_id: pending.confirmation_id,
    question: pending.question,
    reason_code: typeof pending.reason_code === "string" ? pending.reason_code : undefined,
    required_fields: Array.isArray(pending.required_fields)
      ? pending.required_fields.filter((value): value is string => typeof value === "string")
      : undefined,
    expires_at: typeof pending.expires_at === "string" ? pending.expires_at : undefined,
    assistant_message_id: assistantMessageId,
  };
}

function harnessStatusErrorMessage(status: Record<string, unknown>, fallback: string) {
  const lastError = asRecord(status.last_error);
  return typeof lastError.message === "string" && lastError.message.trim()
    ? lastError.message
    : fallback;
}

async function reconcileHarnessStreamFailure(
  runId: string | undefined,
  assistantMessageId: string,
  fallbackMessage: string,
) {
  if (!runId) {
    return {
      status: undefined,
      pendingConfirmation: undefined,
      errorMessage: fallbackMessage,
    };
  }

  try {
    const status = await harnessService.status(runId, "dev-user");
    const statusValue = typeof status.status === "string" ? status.status : undefined;
    return {
      status: statusValue,
      pendingConfirmation: statusValue === "waiting_confirmation"
        ? confirmationFromHarnessStatus(status, runId, assistantMessageId)
        : undefined,
      errorMessage: harnessStatusErrorMessage(status, fallbackMessage),
    };
  } catch (error) {
    return {
      status: undefined,
      pendingConfirmation: undefined,
      errorMessage: error instanceof Error
        ? `流式连接失败，且无法查询 Harness 状态：${error.message}`
        : "流式连接失败，且无法查询 Harness 状态。",
    };
  }
}

function HarnessConfirmationPanel({
  confirmation,
  isRunning,
  onSubmit,
}: {
  confirmation: PendingHarnessConfirmation;
  isRunning: boolean;
  onSubmit: (answer: string, decision: "confirm" | "reject") => Promise<void>;
}) {
  const [answer, setAnswer] = useState("");

  useEffect(() => {
    setAnswer("");
  }, [confirmation.confirmation_id]);

  return (
    <div className="shrink-0 border-t border-amber-200 bg-amber-50 px-6 py-4">
      <div className="text-xs font-bold text-amber-900">需要确认后继续</div>
      <div className="mt-1 text-sm leading-6 text-amber-950">{confirmation.question}</div>
      {confirmation.required_fields?.length ? (
        <div className="mt-2 text-[11px] text-amber-800">需要补充：{confirmation.required_fields.join("、")}</div>
      ) : null}
      <textarea
        value={answer}
        onChange={(event) => setAnswer(event.target.value)}
        disabled={isRunning}
        rows={2}
        placeholder="输入确认内容，或说明不继续"
        className="mt-3 min-h-[64px] w-full resize-y rounded-lg border border-amber-300 bg-white px-3 py-2 text-sm text-slate-800 outline-none focus:border-amber-500"
      />
      <div className="mt-3 flex justify-end gap-2">
        <button
          type="button"
          disabled={isRunning}
          onClick={() => void onSubmit(answer.trim() || "不继续", "reject")}
          className="rounded-lg border border-slate-300 bg-white px-3 py-2 text-xs font-bold text-slate-600 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          停止任务
        </button>
        <button
          type="button"
          disabled={isRunning || !answer.trim()}
          onClick={() => void onSubmit(answer.trim(), "confirm")}
          className="rounded-lg bg-amber-600 px-3 py-2 text-xs font-bold text-white hover:bg-amber-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          确认并继续
        </button>
      </div>
    </div>
  );
}

function getAppendMessageText(message: AppendMessage) {
  /** 从 assistant-ui 新消息结构中提取用户输入的文本。 */

  return message.content
    .map((part) => {
      if (part.type === "text") return part.text;
      return "";
    })
    .join("")
    .trim();
}

// 发送events请求
function AssistantChatRuntime({
  messages,
  isRunning,
  children,
  onNew,
  onCancel,
}: AssistantChatRuntimeProps) {
  /** 使用 assistant-ui 的 external store runtime 接管外部消息状态和发送事件。 */

  const runtime = useExternalStoreRuntime<ThreadMessageLike>({
    messages,
    isRunning,
    onNew,
    onCancel,
    convertMessage: (message) => message as ThreadMessage,
  });

  return <AssistantRuntimeProvider runtime={runtime}>{children}</AssistantRuntimeProvider>;
}

function AssistantMessageBubble() {
  /** 自定义 assistant-ui 消息气泡，保持当前项目的蓝白聊天样式。 */

  const message = useMessage();
  const threadIsRunning = useAuiState((state) => state.thread.isRunning);
  const lastMessageId = useAuiState((state) => state.thread.messages.at(-1)?.id);
  const isUser = message.role === "user";
  const meta = conversationMeta(message);
  const executionProcess = useContext(ExecutionProcessContext);
  const liveEvents = useContext(RunProgressContext);
  const displayText = getDisplayMessageText(message);
  // Harness 每个轮次都会落 execution_trace，所以统一提供入口；旧图轮次保持原有判断。
  const traceTurnId = meta?.turn_id;
  const canViewExecution = !!meta
    && traceTurnId != null
    && (meta.response_type === "analysis"
      || meta.response_type === "simple_data"
      || meta.execution_mode === "harness");
  const traceActive = executionProcess?.open === true
    && executionProcess.activeTurnId === meta?.turn_id;
  const executionLabel = !canViewExecution
    ? ""
    : meta.response_type === "analysis"
    ? traceActive ? "收起分析过程" : "查看分析过程"
    : meta.response_type === "simple_data"
    ? traceActive ? "收起查询过程" : "查看查询过程"
    : traceActive ? "收起执行过程" : "查看执行过程";
  const showStreamingCursor =
    threadIsRunning &&
    !isUser &&
    message.id === lastMessageId &&
    message.status?.type === "running";
  // 占位消息（空文本或恢复流提示语）期间气泡内展示实时运行进度，打字机输出开始后让位给正文。
  const isLivePlaceholder =
    showStreamingCursor
    && (displayText.trim() === "" || displayText.trim() === "正在根据你的确认继续处理...");
  const metaElapsedSeconds = typeof meta?.elapsed_seconds === "number" ? meta.elapsed_seconds : 0;
  const showSummaryChip = !!meta
    && !isLivePlaceholder
    && meta.response_type !== "chat"
    && metaElapsedSeconds > 0;
  const traceToggle = executionProcess && canViewExecution && traceTurnId
    ? () => executionProcess.toggle(traceTurnId)
    : undefined;

  return (
    <MessagePrimitive.Root className={`w-full flex gap-3 ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser && (
        <div className="w-8 h-8 rounded-xl bg-blue-600 text-white flex items-center justify-center shrink-0">
          <Bot className="w-4 h-4" />
        </div>
      )}

      <div className={`${isUser ? "max-w-[70%]" : "w-full min-w-0"} ${isUser ? "items-end" : "items-start"} flex flex-col`}>
        <div
          className={`rounded-2xl px-4 py-3 text-sm leading-6 whitespace-pre-wrap ${!isUser ? "max-w-[70%]" : ""} ${isUser
              ? "bg-blue-600 text-white shadow-sm"
              : "bg-white text-slate-700 border border-slate-200 shadow-sm"
            }`}
        >
          {isLivePlaceholder ? (
            <ChatRunTimeline events={liveEvents} />
          ) : (
            <>
              {displayText}
              {showStreamingCursor && (
                <span className="ml-1 inline-block w-1.5 h-4 bg-blue-500 animate-pulse align-middle" />
              )}
            </>
          )}
        </div>
        <span className="text-[10px] text-slate-400 mt-1 px-1">
          {formatTime(message.createdAt)}
        </span>
        {meta?.query_result && <QueryResultView result={meta.query_result} />}
        {showSummaryChip ? (
          <ChatRunSummaryChip
            status={meta?.status}
            elapsedSeconds={metaElapsedSeconds}
            traceLabel={executionLabel || undefined}
            onOpenTrace={traceToggle}
          />
        ) : (
          executionProcess && executionLabel && traceTurnId && (
            <button
              type="button"
              onClick={() => executionProcess.toggle(traceTurnId)}
              className="mt-4 inline-flex items-center gap-1.5 rounded-md border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-600 transition-colors hover:border-blue-300 hover:bg-blue-50 hover:text-blue-700"
            >
              <ListTree className="size-3.5" />
              {executionLabel}
              <ChevronRight className={"size-3.5 transition-transform " + (traceActive ? "rotate-180" : "")} />
            </button>
          )
        )}
        {meta?.rendered_report && (
          <div className="mt-6 w-full min-w-0">
            <ReportView report={meta.rendered_report} />
          </div>
        )}
      </div>

      {isUser && (
        <div className="w-8 h-8 rounded-xl bg-slate-200 text-slate-600 flex items-center justify-center shrink-0">
          <User className="w-4 h-4" />
        </div>
      )}
    </MessagePrimitive.Root>
  );
}

// 整体页面
function AssistantChatThread() {
  /** 渲染聊天消息区域和输入框，底层仍使用 assistant-ui primitives。 */

  const isRunning = useAuiState((state) => state.thread.isRunning);

  return (
    <ThreadPrimitive.Root className="flex-1 min-h-0 flex flex-col">
      <ThreadPrimitive.Viewport className="flex-1 overflow-y-auto px-6 py-5 space-y-4">
        <ThreadPrimitive.Empty>
          <div className="rounded-2xl border border-dashed border-slate-300 bg-white px-6 py-10 text-center">
            <Bot className="w-10 h-10 mx-auto text-blue-500 mb-3" />
            <div className="font-extrabold text-slate-800">这是一条真实聊天链路</div>
            <p className="text-sm text-slate-500 mt-2">
              发送一句话，后端会创建 run，LangGraph 执行 chat 节点，并通过 SSE 返回结果。
            </p>
          </div>
        </ThreadPrimitive.Empty>
        {/* 会话信息 */}
        <ThreadPrimitive.Messages components={{ Message: AssistantMessageBubble }} />
      </ThreadPrimitive.Viewport>
      {/* 输入框 */}
      <div className="bg-white border-t border-slate-200 p-4">
        <ComposerPrimitive.Root className="flex gap-3">
          <ComposerPrimitive.Input
            submitMode="enter"
            placeholder="继续提问，例如：先介绍一下你现在能做什么"
            className="min-h-[52px] max-h-[140px] flex-1 resize-none rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-800 outline-none focus:border-blue-500 focus:bg-white"
          />
          {isRunning ? (
            <ComposerPrimitive.Cancel className="w-12 h-12 rounded-xl bg-slate-700 text-white flex items-center justify-center shadow-sm hover:bg-slate-800 transition-colors">
              <Loader2 className="w-5 h-5 animate-spin" />
            </ComposerPrimitive.Cancel>
          ) : (
            <ComposerPrimitive.Send className="w-12 h-12 rounded-xl bg-blue-600 text-white flex items-center justify-center shadow-sm hover:bg-blue-700 disabled:bg-slate-300 disabled:cursor-not-allowed transition-colors">
              <Send className="w-5 h-5" />
            </ComposerPrimitive.Send>
          )}
        </ComposerPrimitive.Root>
      </div>
    </ThreadPrimitive.Root>
  );
}

export default function ChatSessionView({ conversationId }: ChatSessionViewProps) {
  /** 会话详情页：加载历史消息，发送问题，并把本轮事件交给统一执行面板。 */

  const [conversation, setConversation] = useState<any | null>(null);
  const [messages, setMessages] = useState<ThreadMessageLike[]>([]);
  const [debugEvents, setDebugEvents] = useState<DebugEvent[]>([]);
  const [executionMode, setExecutionMode] = useState<ExecutionMode | null>(null);
  const [executionPanelOpen, setExecutionPanelOpen] = useState(false);
  const [activeExecutionTurnId, setActiveExecutionTurnId] = useState<string>();
  const [executionTraceLoading, setExecutionTraceLoading] = useState(false);
  const [executionTraceError, setExecutionTraceError] = useState("");
  const [isRunning, setIsRunning] = useState(false);
  const [activeRunTurnId, setActiveRunTurnId] = useState<string>();
  const [pendingConfirmation, setPendingConfirmation] = useState<PendingHarnessConfirmation>();
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const pendingStartedRef = useRef(false);
  const activeAbortControllerRef = useRef<AbortController | null>(null);
  const executionTraceRequestRef = useRef(0);
  const optimisticMessagesRef = useRef<ThreadMessageLike[]>([]);
  const activeTurnIdRef = useRef<string | undefined>(undefined);

  const loadConversation = useCallback(async () => {
    /** 同时拉取会话详情和消息列表，用于刷新标题与聊天记录。 */

    try {
      const data = await conversationService.detail(conversationId);
      const history = data as ConversationHistoryData;
      const turnsById = new Map(
        (history.turns ?? []).map((turn) => [turn.turn_id, turn]),
      );
      const groupedOutputs = outputsByTurn(history);
      const latestTurn = history.turns?.at(-1);

      setConversation(history.conversation ?? null);
      setExecutionMode(latestTurn?.execution_mode ?? null);
      const historyMessages = (history.messages ?? []).map((message) =>
          toAssistantMessage(
            message,
            message.turn_id
              ? displayOutputForMessage(groupedOutputs.get(message.turn_id))
              : undefined,
            message.turn_id ? turnsById.get(message.turn_id) : undefined,
          ),
        );
      const activeTurnId = activeTurnIdRef.current;
      const currentTurnPersisted = Boolean(activeTurnId && turnsById.has(activeTurnId));
      if (currentTurnPersisted) optimisticMessagesRef.current = [];
      const localMessages = optimisticMessagesRef.current.filter(
        (local) => !historyMessages.some((historyMessage) => historyMessage.id === local.id),
      );
      setMessages(currentTurnPersisted || !localMessages.length
        ? historyMessages
        : [...historyMessages, ...localMessages]);

      const activeRunId = typeof asRecord(history.conversation).active_run_id === "string"
        ? String(asRecord(history.conversation).active_run_id)
        : latestTurn?.run_id;
      if (activeRunId) {
        try {
          const runStatus = await harnessService.status(activeRunId, "dev-user");
          const pending = asRecord(runStatus?.pending_confirmation);
          if (
            runStatus?.status === "waiting_confirmation"
            && typeof pending.confirmation_id === "string"
            && typeof pending.question === "string"
          ) {
            setPendingConfirmation({
              run_id: activeRunId,
              confirmation_id: pending.confirmation_id,
              question: pending.question,
              reason_code: typeof pending.reason_code === "string" ? pending.reason_code : undefined,
              required_fields: Array.isArray(pending.required_fields)
                ? pending.required_fields.filter((value): value is string => typeof value === "string")
                : undefined,
              expires_at: typeof pending.expires_at === "string" ? pending.expires_at : undefined,
              assistant_message_id: "",
            });
            setExecutionMode("clarification");
          }
        } catch {
          // 运行状态不是会话历史的强依赖；状态接口不可用时仍显示历史消息。
        }
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "无法加载会话历史";
      setError(`会话历史加载失败：${message}`);
    } finally {
      setIsLoading(false);
    }
  }, [conversationId]);

  const sendQuestion = useCallback(
    async (question: string, content?: AppendMessage["content"]) => {
      /** 发送用户问题：本地乐观插入消息，创建 run，读取 SSE，并在完成后回源刷新。 */

      const normalizedQuestion = question.trim();
      if (!normalizedQuestion || isRunning) return;

      const userMessage: ThreadMessageLike = {
        id: `local_user_${crypto.randomUUID()}`,
        role: "user",
        content: content ?? [{ type: "text", text: normalizedQuestion }],
        createdAt: new Date(),
      };
      const assistantMessageId = `local_assistant_${crypto.randomUUID()}`;
      const assistantMessage: ThreadMessageLike = {
        id: assistantMessageId,
        role: "assistant",
        content: [{ type: "text", text: "" }],
        createdAt: new Date(),
        status: { type: "running" },
      } as ThreadMessageLike;

      setError("");
      setDebugEvents([]);
      setExecutionMode(null);
      setExecutionPanelOpen(false);
      setActiveExecutionTurnId(undefined);
      setExecutionTraceLoading(false);
      setExecutionTraceError("");
      setActiveRunTurnId(undefined);
      executionTraceRequestRef.current += 1;
      setPendingConfirmation(undefined);
      setIsRunning(true);
      setMessages((current) => [...current, userMessage, assistantMessage]);
      optimisticMessagesRef.current = [userMessage, assistantMessage];

      const abortController = new AbortController();
      activeAbortControllerRef.current = abortController;

      let assistantText = "";
      let visibleAssistantText = "";
      let failedMessage = "";
      let typewriterRunning = false;
      let responseMeta: ConversationTurnMeta | undefined;
      let currentTurnId: string | undefined;
      let currentRunId: string | undefined;
      const startedAt = Date.now();
      let terminalEventReceived = false;
      let waitingForConfirmation = false;
      let streamFailureMessage = "";

      const updateAssistantMessage = (text: string, status: ThreadMessageLike["status"]) => {
        /** 更新本地 assistant 消息，用于打字机逐步刷新气泡内容。 */

        setMessages((current) =>
          current.map((message) =>
            message.id === assistantMessageId
              ? ({
                  ...message,
                  content: [{ type: "text", text }],
                  status,
                  ...(responseMeta
                    ? { metadata: { custom: { conversation: responseMeta } } }
                    : {}),
                } as ThreadMessageLike)
              : message,
          ),
        );
      };

      const drainTypewriter = async () => {
        /** 把 SSE 收到的完整目标文本按小步长渲染出来，形成打字机效果。 */

        if (typewriterRunning) return;
        typewriterRunning = true;

        try {
          while (!abortController.signal.aborted && visibleAssistantText.length < assistantText.length) {
            const remainingLength = assistantText.length - visibleAssistantText.length;
            const step = remainingLength > 80 ? 4 : remainingLength > 30 ? 3 : 2;
            visibleAssistantText = assistantText.slice(0, visibleAssistantText.length + step);
            updateAssistantMessage(visibleAssistantText, { type: "running" });
            await sleep(18);
          }
        } finally {
          typewriterRunning = false;
        }
      };

      const waitForTypewriterIdle = async () => {
        /** 等待缓冲区完全渲染，避免 run.completed 后最后几个字还没显示完。 */

        while (!abortController.signal.aborted && (typewriterRunning || visibleAssistantText.length < assistantText.length)) {
          if (!typewriterRunning) {
            void drainTypewriter();
          }
          await sleep(18);
        }
      };

      try {
        for await (const event of harnessService.streamRun(
          {
            conversation_id: conversationId,
            input_text: normalizedQuestion,
            user_id: "dev-user",
          },
          { signal: abortController.signal },
        )) {
          const runEvent = toHarnessStreamEvent(event);
          setDebugEvents((current) => appendExecutionEvent(current, runEvent));
          // 事件始终记录到调试缓冲，供用户手动打开查看；
          // 但只有真实工具执行时才自动展开执行过程，纯聊天（直接 final_answer）不弹面板。
          if (
            runEvent.type.startsWith("context.")
            || runEvent.type.startsWith("planner.")
            || runEvent.type === "action.committed"
            || runEvent.type.startsWith("tool.")
          ) {
            setExecutionPanelOpen(true);
          }
          if (runEvent.run_ref?.turn_id) {
            currentTurnId = runEvent.run_ref.turn_id;
            activeTurnIdRef.current = currentTurnId;
            setActiveRunTurnId(currentTurnId);
            setActiveExecutionTurnId(currentTurnId);
          }
          if (runEvent.run_ref?.run_id) {
            currentRunId = runEvent.run_ref.run_id;
          }

          if (runEvent.type === "stream.failed") {
            const fallback = typeof runEvent.message === "string"
              ? runEvent.message
              : "Harness 流式连接失败。";
            const reconciled = await reconcileHarnessStreamFailure(
              currentRunId,
              assistantMessageId,
              fallback,
            );
            if (reconciled.status === "waiting_confirmation" && reconciled.pendingConfirmation) {
              const pending = reconciled.pendingConfirmation;
              waitingForConfirmation = true;
              terminalEventReceived = true;
              assistantText = pending.question;
              visibleAssistantText = assistantText;
              responseMeta = buildConversationMeta(
                assistantText,
                "clarification",
                { question: assistantText },
                { turn_id: currentTurnId, execution_mode: "clarification", status: "waiting_confirmation" },
                Math.max(0, (Date.now() - startedAt) / 1000),
              );
              setExecutionMode("clarification");
              setPendingConfirmation(pending);
              updateAssistantMessage(assistantText, { type: "complete", reason: "stop" });
            } else if (reconciled.status === "completed") {
              terminalEventReceived = true;
            } else if (
              reconciled.status === "failed"
              || reconciled.status === "timeout"
              || reconciled.status === "cancelled"
            ) {
              terminalEventReceived = true;
              failedMessage = reconciled.errorMessage;
            } else {
              streamFailureMessage = reconciled.status === "running"
                ? fallback + " Harness 仍在运行，请稍后刷新查看最终状态。"
                : reconciled.errorMessage;
            }
            continue;
          }

          if (runEvent.type === "confirmation.required") {
            const pending = confirmationFromHarnessEvent(runEvent, assistantMessageId);
            if (pending) {
              waitingForConfirmation = true;
              terminalEventReceived = true;
              assistantText = pending.question;
              visibleAssistantText = assistantText;
              responseMeta = buildConversationMeta(
                assistantText,
                "clarification",
                { question: assistantText },
                { turn_id: currentTurnId, execution_mode: "clarification", status: "waiting_confirmation" },
                Math.max(0, (Date.now() - startedAt) / 1000),
              );
              setExecutionMode("clarification");
              setPendingConfirmation(pending);
              updateAssistantMessage(assistantText, { type: "complete", reason: "stop" });
            }
            continue;
          }

          if (runEvent.type === "run.result") {
            const loopResult = asRecord(runEvent.loop_result);
            if (loopResult.status === "waiting_confirmation") {
              const pending = confirmationFromHarnessEvent(runEvent, assistantMessageId);
              if (pending) {
                waitingForConfirmation = true;
                terminalEventReceived = true;
                assistantText = pending.question;
                visibleAssistantText = assistantText;
                responseMeta = buildConversationMeta(
                  assistantText,
                  "clarification",
                  { question: assistantText },
                  { turn_id: currentTurnId, execution_mode: "clarification", status: "waiting_confirmation" },
                  Math.max(0, (Date.now() - startedAt) / 1000),
                );
                setExecutionMode("clarification");
                setPendingConfirmation(pending);
                updateAssistantMessage(assistantText, { type: "complete", reason: "stop" });
              }
              continue;
            }
            const finalization = asRecord(loopResult.finalization_result);
            const nestedAnswer = finalization.final_answer;
            if (typeof nestedAnswer === "string" && nestedAnswer.trim()) {
              assistantText = nestedAnswer;
            }
          }

          if (runEvent.type === "run.completed") {
            terminalEventReceived = true;
            const answer = finalAnswerForHarnessEvent(runEvent);
            if (answer) assistantText = answer;
            const outputType = typeof runEvent.final_output_type === "string"
              ? runEvent.final_output_type
              : "text";
            responseMeta = buildConversationMeta(
              assistantText || "任务已完成。",
              outputType,
              { message: assistantText },
              { turn_id: currentTurnId, status: "completed" },
              Math.max(0, (Date.now() - startedAt) / 1000),
            );
            void drainTypewriter();
            await waitForTypewriterIdle();
            updateAssistantMessage(assistantText || "任务已完成。", { type: "complete", reason: "stop" });
            continue;
          }

          if (runEvent.type === "run.failed"
            || runEvent.type === "run.timeout"
            || runEvent.type === "run.cancelled") {
            terminalEventReceived = true;
            failedMessage = finalAnswerForHarnessEvent(runEvent)
              || (typeof runEvent.message === "string" ? runEvent.message : "运行失败");
            responseMeta = buildConversationMeta(
              failedMessage,
              "failure",
              { message: failedMessage },
              { turn_id: currentTurnId, status: runEvent.type.slice(4) },
              Math.max(0, (Date.now() - startedAt) / 1000),
            );
            updateAssistantMessage(failedMessage, { type: "incomplete", reason: "error" });
            continue;
          }
        }

        if (waitingForConfirmation) {
          setIsRunning(false);
        } else if (failedMessage) {
          setError(failedMessage);
          updateAssistantMessage(failedMessage, { type: "incomplete", reason: "error" });
          try { await loadConversation(); } catch { /* 保留本地失败消息。 */ }
          notifyConversationsChanged();
        } else if (streamFailureMessage) {
          setError(streamFailureMessage);
          responseMeta = buildConversationMeta(
            streamFailureMessage,
            "failure",
            { message: streamFailureMessage },
            { turn_id: currentTurnId, status: "stream_failed" },
            Math.max(0, (Date.now() - startedAt) / 1000),
          );
          updateAssistantMessage(streamFailureMessage, { type: "incomplete", reason: "error" });
        } else if (!terminalEventReceived) {
          const message = "执行连接已结束，但没有收到最终状态。";
          setError(message);
          responseMeta = buildConversationMeta(
            message,
            "failure",
            { message },
            { turn_id: currentTurnId },
            Math.max(0, (Date.now() - startedAt) / 1000),
          );
          updateAssistantMessage(message, { type: "incomplete", reason: "error" });
          try {
            await loadConversation();
          } catch {
            // 历史刷新失败时保留本地终态消息，避免覆盖可见错误。
          }
        } else {
          try { await loadConversation(); } catch { /* 保留本地完成消息。 */ }
          notifyConversationsChanged();
        }
      } catch (err) {
        if (!abortController.signal.aborted) {
          const fallback = err instanceof Error ? err.message : "运行流式连接失败";
          const reconciled = await reconcileHarnessStreamFailure(
            currentRunId,
            assistantMessageId,
            fallback,
          );
          if (reconciled.status === "waiting_confirmation" && reconciled.pendingConfirmation) {
            setPendingConfirmation(reconciled.pendingConfirmation);
            setExecutionMode("clarification");
            updateAssistantMessage(reconciled.pendingConfirmation.question, { type: "complete", reason: "stop" });
          } else if (reconciled.status === "completed") {
            try {
              await loadConversation();
              notifyConversationsChanged();
            } catch {
              const message = "任务已完成，但前端未能同步最终结果。";
              setError(message);
              updateAssistantMessage(message, { type: "incomplete", reason: "error" });
            }
          } else {
            const message = reconciled.status === "running"
              ? fallback + " Harness 仍在运行，请稍后刷新查看最终状态。"
              : reconciled.errorMessage;
            setError(message);
            responseMeta = buildConversationMeta(
              message,
              "failure",
              { message },
              { turn_id: currentTurnId, status: reconciled.status || "stream_failed" },
              Math.max(0, (Date.now() - startedAt) / 1000),
            );
            updateAssistantMessage(message, { type: "incomplete", reason: "error" });
            try { await loadConversation(); } catch { /* 保留本地流错误。 */ }
          }
        }
      } finally {
        if (activeAbortControllerRef.current === abortController) {
          activeAbortControllerRef.current = null;
        }
        setActiveRunTurnId(undefined);
        setIsRunning(false);
      }
    },
    [conversationId, isRunning, loadConversation],
  );

  const resumeConfirmation = useCallback(
    async (answer: string, decision: "confirm" | "reject") => {
      /** 提交确认并恢复同一个 Harness run，不创建新的 turn 或 conversation。 */

      const confirmation = pendingConfirmation;
      if (!confirmation || isRunning) return;

      setError("");
      setIsRunning(true);
      setExecutionPanelOpen(true);
      setExecutionMode(null);

      const abortController = new AbortController();
      activeAbortControllerRef.current = abortController;
      const assistantMessageId = confirmation.assistant_message_id || `local_assistant_${crypto.randomUUID()}`;
      if (confirmation.assistant_message_id) {
        setMessages((current) => current.map((message) => (
          message.id === assistantMessageId
            ? ({
                ...message,
                content: [{ type: "text", text: "正在根据你的确认继续处理..." }],
                status: { type: "running" },
              } as ThreadMessageLike)
            : message
        )));
      } else {
        setMessages((current) => [
          ...current,
          {
            id: assistantMessageId,
            role: "assistant",
            content: [{ type: "text", text: "正在根据你的确认继续处理..." }],
            createdAt: new Date(),
            status: { type: "running" },
          } as ThreadMessageLike,
        ]);
      }

      let assistantText = "正在根据你的确认继续处理...";
      let failedMessage = "";
      let currentTurnId: string | undefined;
      let terminalEventReceived = false;
      let waitingForConfirmation = false;
      let nextConfirmation: PendingHarnessConfirmation | undefined;
      let streamFailureMessage = "";
      const startedAt = Date.now();
      let responseMeta: ConversationTurnMeta | undefined;

      const updateAssistantMessage = (text: string, status: ThreadMessageLike["status"]) => {
        setMessages((current) => current.map((message) => (
          message.id === assistantMessageId
            ? ({
                ...message,
                content: [{ type: "text", text }],
                status,
                ...(responseMeta
                  ? { metadata: { custom: { conversation: responseMeta } } }
                  : {}),
              } as ThreadMessageLike)
            : message
        )));
      };

      try {
        for await (const event of harnessService.streamResume(
          {
            run_id: confirmation.run_id,
            user_id: "dev-user",
            confirmation_id: confirmation.confirmation_id,
            answer: answer.trim(),
            decision,
            resolved_conditions: {},
          },
          { signal: abortController.signal },
        )) {
            const runEvent = toHarnessStreamEvent(event);
            setDebugEvents((current) => appendExecutionEvent(current, runEvent));
            if (runEvent.run_ref?.turn_id) {
              currentTurnId = runEvent.run_ref.turn_id;
              activeTurnIdRef.current = currentTurnId;
              setActiveRunTurnId(currentTurnId);
              setActiveExecutionTurnId(currentTurnId);
            }

            if (runEvent.type === "stream.failed") {
              const fallback = typeof runEvent.message === "string"
                ? runEvent.message
                : "Harness 恢复流式连接失败。";
              const reconciled = await reconcileHarnessStreamFailure(
                confirmation.run_id,
                assistantMessageId,
                fallback,
              );
              if (reconciled.status === "waiting_confirmation" && reconciled.pendingConfirmation) {
                nextConfirmation = reconciled.pendingConfirmation;
                waitingForConfirmation = true;
                terminalEventReceived = true;
                assistantText = nextConfirmation.question;
                responseMeta = buildConversationMeta(
                  assistantText,
                  "clarification",
                  { question: assistantText },
                  { turn_id: currentTurnId, execution_mode: "clarification", status: "waiting_confirmation" },
                  Math.max(0, (Date.now() - startedAt) / 1000),
                );
                setExecutionMode("clarification");
                updateAssistantMessage(assistantText, { type: "complete", reason: "stop" });
              } else if (reconciled.status === "completed") {
                terminalEventReceived = true;
              } else if (
                reconciled.status === "failed"
                || reconciled.status === "timeout"
                || reconciled.status === "cancelled"
              ) {
                terminalEventReceived = true;
                failedMessage = reconciled.errorMessage;
              } else {
                streamFailureMessage = reconciled.status === "running"
                  ? fallback + " Harness 仍在运行，请稍后刷新查看最终状态。"
                  : reconciled.errorMessage;
              }
              continue;
            }

            if (runEvent.type === "confirmation.required") {
              nextConfirmation = confirmationFromHarnessEvent(runEvent, assistantMessageId);
              waitingForConfirmation = Boolean(nextConfirmation);
              if (nextConfirmation) {
                assistantText = nextConfirmation.question;
                responseMeta = buildConversationMeta(
                  assistantText,
                  "clarification",
                  { question: assistantText },
                  { turn_id: currentTurnId, execution_mode: "clarification", status: "waiting_confirmation" },
                  Math.max(0, (Date.now() - startedAt) / 1000),
                );
                setExecutionMode("clarification");
                updateAssistantMessage(assistantText, { type: "complete", reason: "stop" });
              }
              continue;
            }

            if (runEvent.type === "run.result") {
              const loopResult = asRecord(runEvent.loop_result);
              if (loopResult.status === "waiting_confirmation") {
                nextConfirmation = confirmationFromHarnessEvent(runEvent, assistantMessageId);
                waitingForConfirmation = Boolean(nextConfirmation);
                if (nextConfirmation) {
                  assistantText = nextConfirmation.question;
                  responseMeta = buildConversationMeta(
                    assistantText,
                    "clarification",
                    { question: assistantText },
                    { turn_id: currentTurnId, execution_mode: "clarification", status: "waiting_confirmation" },
                    Math.max(0, (Date.now() - startedAt) / 1000),
                  );
                  setExecutionMode("clarification");
                  updateAssistantMessage(assistantText, { type: "complete", reason: "stop" });
                }
                continue;
              }
              const finalization = asRecord(loopResult.finalization_result);
              const nestedAnswer = finalization.final_answer;
              if (typeof nestedAnswer === "string" && nestedAnswer.trim()) {
                assistantText = nestedAnswer;
              }
            }

            if (runEvent.type === "run.completed") {
              terminalEventReceived = true;
              assistantText = finalAnswerForHarnessEvent(runEvent) || assistantText;
              const outputType = typeof runEvent.final_output_type === "string"
                ? runEvent.final_output_type
                : "text";
              responseMeta = buildConversationMeta(
                assistantText,
                outputType,
                { message: assistantText },
                { turn_id: currentTurnId, status: "completed" },
                Math.max(0, (Date.now() - startedAt) / 1000),
              );
              updateAssistantMessage(assistantText, { type: "complete", reason: "stop" });
              continue;
            }

            if (runEvent.type === "run.failed"
              || runEvent.type === "run.timeout"
              || runEvent.type === "run.cancelled") {
              terminalEventReceived = true;
              failedMessage = finalAnswerForHarnessEvent(runEvent)
                || (typeof runEvent.message === "string" ? runEvent.message : "运行失败");
              responseMeta = buildConversationMeta(
                failedMessage,
                "failure",
                { message: failedMessage },
                { turn_id: currentTurnId, status: runEvent.type.slice(4) },
                Math.max(0, (Date.now() - startedAt) / 1000),
              );
              updateAssistantMessage(failedMessage, { type: "incomplete", reason: "error" });
            }
        }

        if (waitingForConfirmation && nextConfirmation) {
          setPendingConfirmation(nextConfirmation);
        } else if (failedMessage) {
          setError(failedMessage);
          try { await loadConversation(); } catch { /* 保留本地失败消息。 */ }
          notifyConversationsChanged();
        } else if (terminalEventReceived) {
          setPendingConfirmation(undefined);
          try { await loadConversation(); } catch { /* 保留本地完成消息。 */ }
          notifyConversationsChanged();
        } else if (streamFailureMessage) {
          setError(streamFailureMessage);
          updateAssistantMessage(streamFailureMessage, { type: "incomplete", reason: "error" });
        } else {
          const message = "恢复连接已结束，但没有收到最终状态。";
          setError(message);
          updateAssistantMessage(message, { type: "incomplete", reason: "error" });
        }
      } catch (err) {
        if (!abortController.signal.aborted) {
          const fallback = err instanceof Error ? err.message : "恢复运行流式连接失败";
          const reconciled = await reconcileHarnessStreamFailure(
            confirmation.run_id,
            assistantMessageId,
            fallback,
          );
          if (reconciled.status === "waiting_confirmation" && reconciled.pendingConfirmation) {
            setPendingConfirmation(reconciled.pendingConfirmation);
            setExecutionMode("clarification");
            updateAssistantMessage(
              reconciled.pendingConfirmation.question,
              { type: "complete", reason: "stop" },
            );
          } else if (reconciled.status === "completed") {
            setPendingConfirmation(undefined);
            try {
              await loadConversation();
              notifyConversationsChanged();
            } catch {
              const message = "任务已完成，但前端未能同步最终结果。";
              setError(message);
              updateAssistantMessage(message, { type: "incomplete", reason: "error" });
            }
          } else {
            const message = reconciled.status === "running"
              ? fallback + " Harness 仍在运行，请稍后刷新查看最终状态。"
              : reconciled.errorMessage;
            setError(message);
            updateAssistantMessage(message, { type: "incomplete", reason: "error" });
          }
        }
      } finally {
        if (activeAbortControllerRef.current === abortController) {
          activeAbortControllerRef.current = null;
        }
        setActiveRunTurnId(undefined);
        setIsRunning(false);
      }
    },
    [isRunning, loadConversation, pendingConfirmation],
  );

  const handleNewMessage = useCallback(
    async (message: AppendMessage) => {
      /** assistant-ui 的新消息回调：提取文本后交给 sendQuestion 执行。 */

      const text = getAppendMessageText(message);
      await sendQuestion(text, message.content);
    },
    [sendQuestion],
  );

  const handleCancel = useCallback(async () => {
    /** 取消当前流式请求，并重置运行状态。 */

    activeAbortControllerRef.current?.abort();
    activeAbortControllerRef.current = null;
    setIsRunning(false);
  }, []);

  useEffect(() => {
    setIsLoading(true);
    setError("");
    setDebugEvents([]);
    setExecutionMode(null);
    setExecutionPanelOpen(false);
    setActiveExecutionTurnId(undefined);
    setExecutionTraceLoading(false);
    setExecutionTraceError("");
    setActiveRunTurnId(undefined);
    setPendingConfirmation(undefined);
    executionTraceRequestRef.current += 1;
    pendingStartedRef.current = false;
    optimisticMessagesRef.current = [];
    activeTurnIdRef.current = undefined;
    activeAbortControllerRef.current?.abort();
    activeAbortControllerRef.current = null;
    void loadConversation();
  }, [conversationId, loadConversation]);

  useEffect(() => {
    if (isLoading || pendingStartedRef.current) return;

    const pendingKey = `pending_question:${conversationId}`;
    const pendingQuestion = sessionStorage.getItem(pendingKey);
    if (!pendingQuestion) return;

    pendingStartedRef.current = true;
    sessionStorage.removeItem(pendingKey);
    void sendQuestion(pendingQuestion);
  }, [conversationId, isLoading, sendQuestion]);

  const hasProcessOutput = messages.some((message) => {
    const meta = conversationMeta(message);
    return meta?.response_type === "analysis" || meta?.response_type === "simple_data";
  });
  const showExecutionPanel = executionPanelOpen && (
    isRunning
    || Boolean(activeRunTurnId)
    || debugEvents.length > 0
    || shouldShowExecutionPanel(executionMode)
    || hasProcessOutput
    || Boolean(pendingConfirmation)
  );

  const toggleExecutionProcess = useCallback((turnId?: string) => {
    /** 打开指定轮次的执行过程；当前运行复用内存事件，历史轮次按需读取。 */

    if (executionPanelOpen && activeExecutionTurnId === turnId) {
      executionTraceRequestRef.current += 1;
      setExecutionTraceLoading(false);
      setExecutionTraceError("");
      setExecutionPanelOpen(false);
      return;
    }

    setActiveExecutionTurnId(turnId);
    setExecutionTraceError("");
    setExecutionPanelOpen(true);

    if (turnId && turnId === activeRunTurnId && isRunning) {
      setExecutionTraceLoading(false);
      return;
    }

    if (!turnId) {
      setDebugEvents([]);
      setExecutionTraceLoading(false);
      return;
    }

    const requestId = executionTraceRequestRef.current + 1;
    executionTraceRequestRef.current = requestId;
    setDebugEvents([]);
    setExecutionTraceLoading(true);
    void conversationService.executionTrace(conversationId, turnId)
      .then((data) => {
        if (executionTraceRequestRef.current !== requestId) return;
        const payload = asRecord(data?.payload);
        const traceEvents = Array.isArray(payload.events)
          ? payload.events
            .map(toHistoricalStreamEvent)
            .filter((event): event is StreamEvent => Boolean(event))
          : [];
        setDebugEvents(traceEvents.reduce<DebugEvent[]>(
          (events, event) => appendExecutionEvent(events, event),
          [],
        ));
        if (data?.available === false || !traceEvents.length) {
          setExecutionTraceError("该轮次没有保存执行过程。");
        }
      })
      .catch((err) => {
        if (executionTraceRequestRef.current !== requestId) return;
        setExecutionTraceError(err instanceof Error ? err.message : "执行过程加载失败");
      })
      .finally(() => {
        if (executionTraceRequestRef.current === requestId) {
          setExecutionTraceLoading(false);
        }
      });
  }, [activeExecutionTurnId, activeRunTurnId, conversationId, executionPanelOpen, isRunning]);

  return (
    <ExecutionProcessContext.Provider
      value={{
        open: executionPanelOpen,
        activeTurnId: activeExecutionTurnId,
        toggle: toggleExecutionProcess,
      }}
    >
      <RunProgressContext.Provider value={debugEvents}>
      <div className="flex-1 flex overflow-hidden bg-slate-50">
        <section className="flex-1 min-w-0 flex flex-col border-r border-slate-200">
        <div className="bg-white border-b border-slate-200 px-6 py-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-[11px] font-bold text-blue-600 mb-1">
                <MessageSquare className="w-4 h-4" />
                <span>聊天会话</span>
              </div>
              <h2 className="text-xl font-extrabold text-slate-900">
                {conversation?.title ?? "加载会话中..."}
              </h2>
              <p className="text-xs text-slate-500 mt-1">支持日常聊天、简单问数和数据分析。</p>
            </div>

            <div className="rounded-xl border border-slate-200 bg-slate-50 px-3 py-2 text-[11px] text-slate-500">
              <div>
                Conversation: <span className="font-mono text-slate-700">{conversationId}</span>
              </div>
              <div>
                Thread:{" "}
                <span className="font-mono text-slate-700">{conversation?.thread_id ?? "--"}</span>
              </div>
            </div>
          </div>
        </div>

        {isLoading ? (
          <div className="flex-1 flex items-center justify-center text-sm font-bold text-slate-400">
            加载会话中...
          </div>
        ) : (
          <AssistantChatRuntime
            messages={messages}
            isRunning={isRunning}
            onNew={handleNewMessage}
            onCancel={handleCancel}
          >
            <AssistantChatThread />
          </AssistantChatRuntime>
        )}

        {pendingConfirmation && (
          <HarnessConfirmationPanel
            confirmation={pendingConfirmation}
            isRunning={isRunning}
            onSubmit={resumeConfirmation}
          />
        )}

        {error && (
          <div className="mx-6 mb-3 rounded-xl border border-rose-100 bg-rose-50 px-4 py-3 text-xs font-bold text-rose-600 flex items-center gap-2">
            <AlertCircle className="w-4 h-4" />
            {error}
          </div>
        )}
        </section>

        {showExecutionPanel && (
          <ExecutionPanel
            running={isRunning}
            debugEvents={debugEvents}
            loading={executionTraceLoading}
            error={executionTraceError}
          />
        )}
      </div>
      </RunProgressContext.Provider>
    </ExecutionProcessContext.Provider>
  );
}
