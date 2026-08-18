"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
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
  CheckCircle2,
  Clock,
  Database,
  Loader2,
  MessageSquare,
  Send,
  User,
} from "lucide-react";
import { apiGet } from "../../lib/api";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../ui/tabs";

interface ChatSessionViewProps {
  conversationId: string;
}

interface RunStep {
  step: string;
  name: string;
  status: "running" | "completed" | "failed";
  summary?: string;
}

interface SqlReviewState {
  passed?: boolean;
  risk_level?: string;
  summary?: string;
  checks?: Array<{
    name: string;
    passed: boolean;
  }>;
}

interface TableArtifactState {
  title?: string;
  columns: string[];
  rows: Array<Record<string, string | number | boolean | null>>;
  row_count?: number;
  elapsed_ms?: number;
}

interface SqlGenerationState {
  generation_mode?: string;
  reasoning?: string;
  tables?: string[];
  metrics?: string[];
  dimensions?: string[];
  expected_limit?: number;
}

interface SourcesState {
  tables: string[];
  fields: string[];
  metrics: string[];
  dimensions: string[];
  semantic_source?: string;
  conversation_asset_count?: number;
  used_global_assets?: boolean;
  used_conversation_assets?: boolean;
  vector_fallback_used?: boolean;
  stale_vector_record?: boolean;
}

interface AuditState {
  risk_level?: string;
  review_passed?: boolean;
  checks?: Array<{
    name: string;
    passed: boolean;
  }>;
  row_count?: number;
  elapsed_ms?: number;
  limit?: number;
  execution_status?: string;
  error?: string | null;
  human_confirmation_required?: boolean;
  vector_fallback_used?: boolean;
  stale_vector_record?: boolean;
}

interface BackendMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  created_at?: string;
}

interface AssistantChatRuntimeProps {
  messages: ThreadMessageLike[];
  isRunning: boolean;
  children: React.ReactNode;
  onNew: (message: AppendMessage) => Promise<void>;
  onCancel: () => Promise<void>;
}

function formatTime(value?: Date | string) {
  /** 格式化消息时间，用于聊天气泡下方展示。 */

  if (!value) return "--";
  return new Date(value).toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function upsertStep(steps: RunStep[], next: RunStep) {
  /** 插入或更新执行步骤，保证同一个 step 在右侧详情中只出现一次。 */

  const index = steps.findIndex((step) => step.step === next.step);
  if (index === -1) return [...steps, next];

  const cloned = [...steps];
  cloned[index] = { ...cloned[index], ...next };
  return cloned;
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

function getDisplayMessageText(message: ThreadMessage | ThreadMessageLike) {
  /** 生成最终展示文本，并去掉流式结束后可能残留的独立圆点。 */

  // Some streaming renderers/providers append a standalone bullet as a pending marker.
  // Only remove an isolated trailing bullet so normal markdown/list content is preserved.
  return getMessageText(message).replace(/\s+[●•]\s*$/, "");
}

function toAssistantMessage(message: BackendMessage): ThreadMessageLike {
  /** 把后端消息模型转换为 assistant-ui 可渲染的消息结构。 */

  return {
    id: message.id,
    role: message.role,
    content: [{ type: "text", text: message.content }],
    createdAt: message.created_at ? new Date(message.created_at) : new Date(),
  };
}

function notifyConversationsChanged() {
  /** 通知侧边栏重新拉取会话列表，保持标题和更新时间同步。 */

  window.dispatchEvent(new CustomEvent("insight:conversations:changed"));
}

function sleep(ms: number) {
  /** 等待指定毫秒数，用于控制前端打字机渲染节奏。 */

  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function* streamAnalysisEvents(
  conversationId: string,
  question: string,
  abortSignal?: AbortSignal,
) {
  /** 通过固定分析入口提交问题，并按事件块逐条读取 SSE 响应。 */

  const response = await fetch("/api/analysis", {
    method: "POST",
    cache: "no-store",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      conversation_id: conversationId,
      question,
    }),
    signal: abortSignal,
  });

  if (!response.ok || !response.body) {
    throw new Error("SSE 连接失败，请检查后端服务是否仍在运行。");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const chunks = buffer.split("\n\n");
    buffer = chunks.pop() ?? "";

    for (const chunk of chunks) {
      const dataLines = chunk
        .split("\n")
        .filter((line) => line.startsWith("data: "))
        .map((line) => line.slice("data: ".length));

      if (dataLines.length === 0) continue;
      yield JSON.parse(dataLines.join("\n"));
    }
  }

  if (buffer.trim()) {
    const dataLines = buffer
      .split("\n")
      .filter((line) => line.startsWith("data: "))
      .map((line) => line.slice("data: ".length));

    if (dataLines.length > 0) {
      yield JSON.parse(dataLines.join("\n"));
    }
  }
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
  const displayText = getDisplayMessageText(message);
  const showStreamingCursor =
    threadIsRunning &&
    !isUser &&
    message.id === lastMessageId &&
    message.status?.type === "running";

  return (
    <MessagePrimitive.Root className={`flex gap-3 ${isUser ? "justify-end" : "justify-start"}`}>
      {!isUser && (
        <div className="w-8 h-8 rounded-xl bg-blue-600 text-white flex items-center justify-center shrink-0">
          <Bot className="w-4 h-4" />
        </div>
      )}

      <div className={`max-w-[70%] ${isUser ? "items-end" : "items-start"} flex flex-col`}>
        <div
          className={`rounded-2xl px-4 py-3 text-sm leading-6 whitespace-pre-wrap ${isUser
              ? "bg-blue-600 text-white shadow-sm"
              : "bg-white text-slate-700 border border-slate-200 shadow-sm"
            }`}
        >
          {displayText}
          {showStreamingCursor && (
            <span className="ml-1 inline-block w-1.5 h-4 bg-blue-500 animate-pulse align-middle" />
          )}
        </div>
        <span className="text-[10px] text-slate-400 mt-1 px-1">
          {formatTime(message.createdAt)}
        </span>
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
  /** 会话详情页：加载历史消息，发送问题，订阅 run SSE，并展示执行步骤。 */

  const [conversation, setConversation] = useState<any | null>(null);
  const [messages, setMessages] = useState<ThreadMessageLike[]>([]);
  const [steps, setSteps] = useState<RunStep[]>([]);
  const [activeRunId, setActiveRunId] = useState("");
  const [generatedSql, setGeneratedSql] = useState("");
  const [sqlGeneration, setSqlGeneration] = useState<SqlGenerationState | null>(null);
  const [sqlReview, setSqlReview] = useState<SqlReviewState | null>(null);
  const [tableArtifact, setTableArtifact] = useState<TableArtifactState | null>(null);
  const [sources, setSources] = useState<SourcesState | null>(null);
  const [audit, setAudit] = useState<AuditState | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [error, setError] = useState("");
  const [isLoading, setIsLoading] = useState(true);
  const pendingStartedRef = useRef(false);
  const activeAbortControllerRef = useRef<AbortController | null>(null);

  const loadConversation = useCallback(async () => {
    /** 同时拉取会话详情和消息列表，用于刷新标题与聊天记录。 */

    const data = await apiGet("/api/conversations", {
      conversation_id: conversationId,
      include_messages: true,
    });

    setConversation(data.conversation);
    setMessages((data.messages ?? []).map(toAssistantMessage));
    setIsLoading(false);
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
      setSteps([]);
      setActiveRunId("");
      setGeneratedSql("");
      setSqlGeneration(null);
      setSqlReview(null);
      setTableArtifact(null);
      setSources(null);
      setAudit(null);
      setIsRunning(true);
      setMessages((current) => [...current, userMessage, assistantMessage]);

      const abortController = new AbortController();
      activeAbortControllerRef.current = abortController;

      let assistantText = "";
      let visibleAssistantText = "";
      let failedMessage = "";
      let typewriterRunning = false;

      const updateAssistantMessage = (text: string, status: ThreadMessageLike["status"]) => {
        /** 更新本地 assistant 消息，用于打字机逐步刷新气泡内容。 */

        setMessages((current) =>
          current.map((message) =>
            message.id === assistantMessageId
              ? ({
                  ...message,
                  content: [{ type: "text", text }],
                  status,
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
        setActiveRunId(crypto.randomUUID());

        for await (const runEvent of streamAnalysisEvents(
          conversationId,
          normalizedQuestion,
          abortController.signal,
        )) {
          const payload = runEvent.data ?? {};

          if (runEvent.type === "run.started" && payload.run_id) {
            setActiveRunId(payload.run_id);
          }

          if (runEvent.type === "step.started") {
            setSteps((current) =>
              upsertStep(current, {
                step: payload.step,
                name: payload.name,
                status: "running",
              }),
            );
          }

          if (runEvent.type === "step.completed") {
            setSteps((current) =>
              upsertStep(current, {
                step: payload.step,
                name: payload.name,
                status: "completed",
                summary: payload.summary,
              }),
            );
          }

          if (runEvent.type === "message.delta" && payload.content) {
            assistantText += payload.content;
            void drainTypewriter();
          }

          if (runEvent.type === "message.completed" && payload.content) {
            assistantText = payload.content;
            void drainTypewriter();
          }

          if (runEvent.type === "sql.generated") {
            setGeneratedSql(payload.sql ?? "");
            setSqlGeneration({
              generation_mode: payload.generation_mode,
              reasoning: payload.reasoning,
              tables: payload.tables ?? [],
              metrics: payload.metrics ?? [],
              dimensions: payload.dimensions ?? [],
              expected_limit: payload.expected_limit,
            });
          }

          if (runEvent.type === "sql.reviewed") {
            setSqlReview({
              passed: payload.passed,
              risk_level: payload.risk_level,
              summary: payload.summary,
              checks: payload.checks ?? [],
            });
          }

          if (runEvent.type === "sources.created") {
            setSources({
              tables: payload.tables ?? [],
              fields: payload.fields ?? [],
              metrics: payload.metrics ?? [],
              dimensions: payload.dimensions ?? [],
              semantic_source: payload.semantic_source,
              conversation_asset_count: payload.conversation_asset_count,
              used_global_assets: payload.used_global_assets,
              used_conversation_assets: payload.used_conversation_assets,
              vector_fallback_used: payload.vector_fallback_used,
              stale_vector_record: payload.stale_vector_record,
            });
          }

          if (runEvent.type === "artifact.created" && payload.artifact_type === "table") {
            setTableArtifact({
              title: payload.title,
              columns: payload.columns ?? [],
              rows: payload.rows ?? [],
              row_count: payload.row_count,
              elapsed_ms: payload.elapsed_ms,
            });
          }

          if (runEvent.type === "audit.created") {
            setAudit({
              risk_level: payload.risk_level,
              review_passed: payload.review_passed,
              checks: payload.checks ?? [],
              row_count: payload.row_count,
              elapsed_ms: payload.elapsed_ms,
              limit: payload.limit,
              execution_status: payload.execution_status,
              error: payload.error,
              human_confirmation_required: payload.human_confirmation_required,
              vector_fallback_used: payload.vector_fallback_used,
              stale_vector_record: payload.stale_vector_record,
            });
          }

          if (runEvent.type === "run.completed") {
            await waitForTypewriterIdle();
            updateAssistantMessage(assistantText, { type: "complete", reason: "stop" });
            setIsRunning(false);
            await loadConversation();
            notifyConversationsChanged();
            break;
          }

          if (runEvent.type === "run.failed") {
            failedMessage = payload.message ?? "运行失败";
            break;
          }
        }

        if (failedMessage) {
          setError(failedMessage);
          updateAssistantMessage(failedMessage, { type: "incomplete", reason: "error" });
        }
      } catch (err) {
        if (!abortController.signal.aborted) {
          const message = err instanceof Error ? err.message : "运行失败";
          setError(message);
          updateAssistantMessage(message, { type: "incomplete", reason: "error" });
        }
      } finally {
        if (activeAbortControllerRef.current === abortController) {
          activeAbortControllerRef.current = null;
        }
        setIsRunning(false);
      }
    },
    [conversationId, isRunning, loadConversation],
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
    setSteps([]);
    setActiveRunId("");
    setGeneratedSql("");
    setSqlGeneration(null);
    setSqlReview(null);
    setTableArtifact(null);
    setSources(null);
    setAudit(null);
    pendingStartedRef.current = false;
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

  return (
    <div className="flex-1 flex overflow-hidden bg-slate-50">
      <section className="flex-1 min-w-0 flex flex-col border-r border-slate-200">
        <div className="bg-white border-b border-slate-200 px-6 py-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="flex items-center gap-2 text-[11px] font-bold text-blue-600 mb-1">
                <MessageSquare className="w-4 h-4" />
                <span>普通聊天最小链路</span>
              </div>
              <h2 className="text-xl font-extrabold text-slate-900">
                {conversation?.title ?? "加载会话中..."}
              </h2>
              <p className="text-xs text-slate-500 mt-1">
                当前使用 assistant-ui 接管消息状态和输入框，后端仍通过 run + SSE 驱动执行详情。
              </p>
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

        {error && (
          <div className="mx-6 mb-3 rounded-xl border border-rose-100 bg-rose-50 px-4 py-3 text-xs font-bold text-rose-600 flex items-center gap-2">
            <AlertCircle className="w-4 h-4" />
            {error}
          </div>
        )}
      </section>

      <aside className="w-[360px] bg-white shrink-0 flex flex-col">
        <div className="p-5 border-b border-slate-200">
          <h3 className="text-sm font-extrabold text-slate-900">执行详情</h3>
          <p className="text-xs text-slate-500 mt-1">Phase 4 + Phase 6 最小链路事件</p>
        </div>

        <div className="p-5 space-y-4 overflow-y-auto">
          <div className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
            <div className="flex items-center gap-2 text-xs font-extrabold text-slate-700 mb-3">
              <Database className="w-4 h-4 text-emerald-600" />
              <span>当前状态</span>
            </div>
            <div className="space-y-2 text-xs text-slate-500">
              <div className="flex justify-between">
                <span>会话状态</span>
                <span className="font-mono text-slate-700">
                  {isRunning ? "running" : conversation?.status ?? "--"}
                </span>
              </div>
              <div className="flex justify-between">
                <span>当前 Run</span>
                <span className="font-mono text-slate-700 truncate max-w-[180px]">
                  {activeRunId || "--"}
                </span>
              </div>
              <div className="flex justify-between">
                <span>数据源</span>
                <span className="font-mono text-slate-700">{conversation?.data_source_id ?? "olist"}</span>
              </div>
            </div>
          </div>

          <Tabs defaultValue="steps" className="min-h-0 flex-1">
            <TabsList
              variant="line"
              className="grid h-9 w-full grid-cols-4 border-b border-slate-200 p-0 text-xs font-bold"
            >
              <TabsTrigger value="steps" className="rounded-none text-xs data-active:text-blue-600">
                Steps
              </TabsTrigger>
              <TabsTrigger value="sql" className="rounded-none text-xs data-active:text-blue-600">
                SQL
              </TabsTrigger>
              <TabsTrigger value="sources" className="rounded-none text-xs data-active:text-blue-600">
                Sources
              </TabsTrigger>
              <TabsTrigger value="audit" className="rounded-none text-xs data-active:text-blue-600">
                Audit
              </TabsTrigger>
            </TabsList>

            <TabsContent value="steps" className="mt-4 space-y-3">
              {steps.length === 0 && (
                <div className="rounded-xl border border-dashed border-slate-200 px-4 py-6 text-center text-xs text-slate-400">
                  等待下一次运行事件
                </div>
              )}
              {steps.map((step) => (
                <div key={step.step} className="rounded-xl border border-slate-200 bg-white p-3">
                  <div className="flex items-center gap-2">
                    {step.status === "completed" ? (
                      <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                    ) : (
                      <Clock className="w-4 h-4 text-blue-600 animate-pulse" />
                    )}
                    <span className="text-sm font-bold text-slate-800">{step.name}</span>
                    <span className="ml-auto text-[10px] font-mono text-slate-400">{step.status}</span>
                  </div>
                  {step.summary && <p className="text-xs text-slate-500 mt-2 leading-5">{step.summary}</p>}
                </div>
              ))}
            </TabsContent>

            <TabsContent value="sql" className="mt-4 space-y-4">
              {sqlGeneration && (
                <div className="rounded-xl border border-blue-100 bg-blue-50/60 p-3 text-xs text-slate-600">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-extrabold text-slate-800">SQL 生成模式</span>
                    <span className="rounded-full bg-white px-2 py-0.5 font-mono text-[10px] font-bold text-blue-600">
                      {sqlGeneration.generation_mode ?? "--"}
                    </span>
                  </div>
                  {sqlGeneration.reasoning && (
                    <p className="mt-2 leading-5 text-slate-500">{sqlGeneration.reasoning}</p>
                  )}
                  <div className="mt-2 space-y-1 font-mono text-[10px] text-slate-500">
                    <div>tables: {(sqlGeneration.tables ?? []).join(", ") || "--"}</div>
                    <div>metrics: {(sqlGeneration.metrics ?? []).join(", ") || "--"}</div>
                    <div>dimensions: {(sqlGeneration.dimensions ?? []).join(", ") || "--"}</div>
                    <div>expected_limit: {sqlGeneration.expected_limit ?? "--"}</div>
                  </div>
                </div>
              )}

              {generatedSql ? (
                <pre className="max-h-[420px] overflow-auto rounded-xl border border-slate-200 bg-slate-950 p-3 text-[11px] leading-5 text-blue-50">
                  <code>{generatedSql}</code>
                </pre>
              ) : (
                <div className="rounded-xl border border-dashed border-slate-200 px-4 py-8 text-center text-xs text-slate-400">
                  等待 SQL 生成事件
                </div>
              )}

              {tableArtifact ? (
                <div className="rounded-xl border border-slate-200 bg-white">
                  <div className="flex items-center justify-between border-b border-slate-100 px-3 py-2">
                    <div>
                      <div className="text-xs font-extrabold text-slate-800">
                        {tableArtifact.title ?? "查询结果"}
                      </div>
                      <div className="mt-0.5 text-[10px] font-medium text-slate-400">
                        返回 {tableArtifact.row_count ?? tableArtifact.rows.length} 行
                        {typeof tableArtifact.elapsed_ms === "number" ? ` · ${tableArtifact.elapsed_ms} ms` : ""}
                      </div>
                    </div>
                  </div>
                  <div className="max-h-[280px] overflow-auto">
                    <table className="w-full min-w-[520px] text-left text-[11px]">
                      <thead className="sticky top-0 bg-slate-50 text-slate-500">
                        <tr>
                          {tableArtifact.columns.map((column) => (
                            <th key={column} className="border-b border-slate-100 px-3 py-2 font-extrabold">
                              {column}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {tableArtifact.rows.map((row, rowIndex) => (
                          <tr key={rowIndex} className="border-b border-slate-50 last:border-0">
                            {tableArtifact.columns.map((column) => (
                              <td key={column} className="px-3 py-2 font-mono text-slate-600">
                                {String(row[column] ?? "--")}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-slate-200 px-4 py-6 text-center text-xs text-slate-400">
                  等待真实查询结果
                </div>
              )}
            </TabsContent>

            <TabsContent value="sources" className="mt-4">
              {sources ? (
                <div className="space-y-3">
                  <div className="rounded-xl border border-slate-200 bg-white p-3">
                    <div className="text-xs font-extrabold text-slate-800">来源概览</div>
                    <div className="mt-3 grid grid-cols-2 gap-2 text-[11px]">
                      <div className="rounded-lg bg-slate-50 p-2">
                        <div className="text-slate-400">语义来源</div>
                        <div className="mt-1 font-mono text-slate-700">{sources.semantic_source ?? "--"}</div>
                      </div>
                      <div className="rounded-lg bg-slate-50 p-2">
                        <div className="text-slate-400">会话资产数</div>
                        <div className="mt-1 font-mono text-slate-700">{sources.conversation_asset_count ?? 0}</div>
                      </div>
                      <div className="rounded-lg bg-slate-50 p-2">
                        <div className="text-slate-400">全局语义资产</div>
                        <div className="mt-1 font-bold text-slate-700">{sources.used_global_assets ? "已使用" : "未使用"}</div>
                      </div>
                      <div className="rounded-lg bg-slate-50 p-2">
                        <div className="text-slate-400">向量兜底</div>
                        <div className="mt-1 font-bold text-slate-700">
                          {sources.vector_fallback_used ? "已使用" : "未使用"}
                        </div>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-xl border border-slate-200 bg-white p-3">
                    <div className="text-xs font-extrabold text-slate-800">数据表</div>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                      {sources.tables.length ? (
                        sources.tables.map((table) => (
                          <span key={table} className="rounded-full bg-blue-50 px-2 py-1 font-mono text-[10px] text-blue-600">
                            {table}
                          </span>
                        ))
                      ) : (
                        <span className="text-xs text-slate-400">暂无表来源</span>
                      )}
                    </div>
                  </div>

                  <div className="rounded-xl border border-slate-200 bg-white p-3">
                    <div className="text-xs font-extrabold text-slate-800">字段</div>
                    <div className="mt-2 max-h-[120px] overflow-auto font-mono text-[10px] leading-5 text-slate-500">
                      {sources.fields.length ? sources.fields.join(", ") : "暂无字段来源"}
                    </div>
                  </div>

                  <div className="rounded-xl border border-slate-200 bg-white p-3">
                    <div className="text-xs font-extrabold text-slate-800">指标 / 维度</div>
                    <div className="mt-2 space-y-1 text-[11px] text-slate-500">
                      <div>metrics: {(sources.metrics ?? []).join(", ") || "--"}</div>
                      <div>dimensions: {(sources.dimensions ?? []).join(", ") || "--"}</div>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-slate-200 px-4 py-8 text-center text-xs leading-5 text-slate-400">
                  等待 Sources 事件
                  <br />
                  Phase 8 会展示使用的数据表、字段、指标和维度来源。
                </div>
              )}
            </TabsContent>

            <TabsContent value="audit" className="mt-4">
              {sqlReview || audit ? (
                <div className="space-y-3">
                  {sqlReview && (
                    <div className="rounded-xl border border-slate-200 bg-white p-3">
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-bold text-slate-800">SQL 审核</span>
                        <span
                          className={`rounded-full px-2 py-0.5 text-[10px] font-black ${
                            sqlReview.passed ? "bg-emerald-50 text-emerald-600" : "bg-rose-50 text-rose-600"
                          }`}
                        >
                          {sqlReview.passed ? "通过" : "未通过"}
                        </span>
                      </div>
                      <p className="mt-2 text-xs leading-5 text-slate-500">{sqlReview.summary}</p>
                      <div className="mt-3 space-y-2">
                        {(sqlReview.checks ?? []).map((check) => (
                          <div key={check.name} className="flex items-center justify-between text-xs">
                            <span className="text-slate-500">{check.name}</span>
                            <span className={check.passed ? "text-emerald-600" : "text-rose-600"}>
                              {check.passed ? "通过" : "失败"}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {audit && (
                    <div className="rounded-xl border border-slate-200 bg-white p-3">
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-bold text-slate-800">执行审计</span>
                        <span className="rounded-full bg-slate-100 px-2 py-0.5 font-mono text-[10px] font-bold text-slate-600">
                          {audit.execution_status ?? "--"}
                        </span>
                      </div>
                      <div className="mt-3 grid grid-cols-2 gap-2 text-[11px]">
                        <div className="rounded-lg bg-slate-50 p-2">
                          <div className="text-slate-400">风险等级</div>
                          <div className="mt-1 font-mono text-slate-700">{audit.risk_level ?? "--"}</div>
                        </div>
                        <div className="rounded-lg bg-slate-50 p-2">
                          <div className="text-slate-400">返回行数</div>
                          <div className="mt-1 font-mono text-slate-700">{audit.row_count ?? 0}</div>
                        </div>
                        <div className="rounded-lg bg-slate-50 p-2">
                          <div className="text-slate-400">执行耗时</div>
                          <div className="mt-1 font-mono text-slate-700">{audit.elapsed_ms ?? 0} ms</div>
                        </div>
                        <div className="rounded-lg bg-slate-50 p-2">
                          <div className="text-slate-400">LIMIT</div>
                          <div className="mt-1 font-mono text-slate-700">{audit.limit ?? "--"}</div>
                        </div>
                      </div>
                      <div className="mt-3 space-y-2 text-xs">
                        <div className="flex items-center justify-between">
                          <span className="text-slate-500">需要人工确认</span>
                          <span className="font-bold text-slate-700">
                            {audit.human_confirmation_required ? "是" : "否"}
                          </span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-slate-500">向量兜底</span>
                          <span className="font-bold text-slate-700">{audit.vector_fallback_used ? "是" : "否"}</span>
                        </div>
                        <div className="flex items-center justify-between">
                          <span className="text-slate-500">向量记录过期</span>
                          <span className="font-bold text-slate-700">{audit.stale_vector_record ? "是" : "否"}</span>
                        </div>
                      </div>
                      {audit.error && (
                        <div className="mt-3 rounded-lg bg-rose-50 px-3 py-2 text-xs text-rose-600">
                          {audit.error}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              ) : (
                <div className="rounded-xl border border-dashed border-slate-200 px-4 py-8 text-center text-xs text-slate-400">
                  等待 SQL 审核事件
                </div>
              )}
            </TabsContent>
          </Tabs>
        </div>
      </aside>
    </div>
  );
}
