"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  Circle,
  Loader2,
  XCircle,
} from "lucide-react";
import {
  buildTasks,
  customEventOf,
  formatDuration,
  progressKind,
  runFailed,
  StatusIcon,
  type DebugEvent,
  type RunStatus,
  type TaskSummary,
} from "./execution-panel";

/** 聊天气泡内的运行进度：与执行面板共用同一份 SSE 事件，纯展示派生，不落库。 */

interface RunStage {
  key: string;
  label: string;
  status: RunStatus;
  detail?: string;
  tasks?: TaskSummary[];
}

interface ThinkingSection {
  key: string;
  label: string;
  text: string;
  done: boolean;
}

function taskTerminal(status: RunStatus) {
  return status === "success" || status === "partial" || status === "failed";
}

function taskStatusText(task: TaskSummary) {
  if (task.status === "running") return task.phase || "执行中";
  if (task.status === "success") return "已完成";
  if (task.status === "failed") return task.error || "执行失败";
  if (task.status === "partial") return task.error || "部分完成";
  return "等待执行";
}

/** 任务划分失败时的原因：planner.failed / analysis_plan 事件里的 error_message 优先。 */
function planFailureMessage(events: DebugEvent[]): string | undefined {
  for (const event of events) {
    if (event.type === "planner.failed" || (customEventOf(event, "analysis_plan") && event.status === "failed")) {
      const message = [event.payload.error_message, event.payload.error].find(
        (value): value is string => typeof value === "string" && value !== "",
      );
      if (message) return message;
    }
  }
  return undefined;
}

/** 从事件序列推导阶段时间线；只展示已开始的阶段，运行失败时把最后一个进行中的阶段标为失败。 */
function deriveRunStages(events: DebugEvent[], tasks: TaskSummary[]): RunStage[] {
  const has = (predicate: (event: DebugEvent) => boolean) => events.some(predicate);
  const stages: RunStage[] = [];

  if (has((event) => event.type.startsWith("context.") || customEventOf(event, "question_route"))) {
    const status: RunStatus = has((event) => event.type === "context.failed")
      ? "failed"
      : has((event) => event.type === "context.completed")
        ? "success"
        : "running";
    stages.push({ key: "understand", label: "理解问题并召回上下文", status });
  }

  if (has((event) => event.type.startsWith("planner.") || customEventOf(event, "analysis_plan"))) {
    const planFailed = has((event) => customEventOf(event, "analysis_plan") && event.status === "failed");
    const status: RunStatus = planFailed || has((event) => event.type === "planner.failed")
      ? "failed"
      : has((event) => event.type === "planner.completed")
        || has((event) => customEventOf(event, "analysis_plan") && event.status === "success")
        ? "success"
        : "running";
    stages.push({
      key: "plan",
      label: "划分分析任务",
      status,
      detail: status === "failed"
        ? planFailureMessage(events)
        : tasks.length ? `${tasks.length} 个任务` : undefined,
    });
  }

  if (tasks.length || has((event) => event.type === "action.committed" || event.type.startsWith("tool."))) {
    if (tasks.length) {
      const done = tasks.filter((task) => taskTerminal(task.status)).length;
      const allDone = done === tasks.length;
      const anyFailed = tasks.some((task) => task.status === "failed" || task.status === "partial");
      stages.push({
        key: "execute",
        label: "执行分析任务",
        status: allDone ? (anyFailed ? "partial" : "success") : "running",
        detail: allDone ? undefined : `已完成 ${done}/${tasks.length}`,
        tasks,
      });
    } else {
      const status: RunStatus = has((event) => event.type === "tool.failed")
        ? "failed"
        : has((event) => event.type === "tool.completed")
          ? "success"
          : "running";
      stages.push({ key: "execute", label: "执行数据查询", status });
    }
  }

  if (has((event) => customEventOf(event, "report_plan_result") || customEventOf(event, "rendered_report"))) {
    const status: RunStatus = has((event) => customEventOf(event, "rendered_report") || event.type === "run.completed")
      ? "success"
      : "running";
    stages.push({ key: "report", label: "汇总生成报告", status });
  }

  if (runFailed(events)) {
    for (let index = stages.length - 1; index >= 0; index -= 1) {
      if (stages[index].status === "running") {
        stages[index].status = "failed";
        break;
      }
    }
  }
  return stages;
}

/** 思考流分节：reasoning_chunk 按节点聚合拼接，reasoning_result 到达即视为该节思考完成。 */
function deriveThinkingSections(events: DebugEvent[]): ThinkingSection[] {
  const sections = new Map<string, ThinkingSection>();
  for (const event of events) {
    if (event.type !== "tool.progress" || progressKind(event) !== "reasoning") continue;
    const key = event.node;
    const section = sections.get(key) ?? {
      key,
      label: typeof event.payload.step === "string" && event.payload.step.trim() ? event.payload.step : key,
      text: "",
      done: false,
    };
    if (event.payload.custom_type === "reasoning_result") {
      section.done = true;
    } else if (typeof event.payload.chunk === "string") {
      section.text += event.payload.chunk;
    }
    sections.set(key, section);
  }
  return Array.from(sections.values());
}

function ThinkingBody({ text, live }: { text: string; live: boolean }) {
  const ref = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (live && ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [text, live]);
  return (
    <div className="relative mt-1">
      <div
        ref={ref}
        className="max-h-24 overflow-y-auto whitespace-pre-wrap rounded-lg bg-slate-50 px-3 py-2 text-xs leading-5 text-slate-600"
      >
        {text || "…"}
      </div>
      {live && (
        <div className="pointer-events-none absolute inset-x-0 bottom-0 h-6 rounded-b-lg bg-gradient-to-t from-slate-50 via-slate-50/70 to-transparent" />
      )}
    </div>
  );
}

function ThinkingSections({ sections }: { sections: ThinkingSection[] }) {
  return (
    <div className="space-y-1.5">
      {sections.map((section, index) => {
        // 正在流式输出的分节默认展开并吸底滚动；完成后重挂载为折叠态，用户可再展开回看。
        const live = !section.done && index === sections.length - 1;
        return (
          <details
            key={section.key + (section.done ? ":done" : ":live")}
            open={live ? true : undefined}
            className="group rounded-lg border border-slate-200 bg-white"
          >
            <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2">
              <ChevronRight className="size-3.5 shrink-0 text-slate-400 transition-transform group-open:rotate-90" />
              {live
                ? <Loader2 className="size-3.5 shrink-0 animate-spin text-blue-500" />
                : <CheckCircle2 className="size-3.5 shrink-0 text-emerald-500" />}
              <span className="min-w-0 truncate text-[11px] font-bold text-slate-600">{section.label}</span>
              <span className="ml-auto shrink-0 text-[10px] text-slate-400">{live ? "思考中" : "已思考"}</span>
            </summary>
            <div className="px-3 pb-2">
              <ThinkingBody text={section.text} live={live} />
            </div>
          </details>
        );
      })}
    </div>
  );
}

function TaskDot({ status }: { status: RunStatus }) {
  if (status === "success") return <CheckCircle2 className="size-3.5 shrink-0 text-emerald-500" />;
  if (status === "failed" || status === "partial") return <XCircle className="size-3.5 shrink-0 text-rose-500" />;
  if (status === "running") return <Loader2 className="size-3.5 shrink-0 animate-spin text-blue-500" />;
  return <Circle className="size-3.5 shrink-0 text-slate-300" />;
}

function TaskChecklist({ tasks }: { tasks: TaskSummary[] }) {
  return (
    <div className="mt-1.5 space-y-1.5 rounded-lg border border-slate-100 bg-slate-50/60 px-2.5 py-2">
      {tasks.map((task) => (
        <div key={task.task_id} className="flex items-start gap-2">
          <span className="mt-0.5"><TaskDot status={task.status} /></span>
          <div className="min-w-0">
            <div className="truncate text-[11px] font-semibold text-slate-600">
              {task.question || task.task_id}
            </div>
            <div className="truncate text-[10px] text-slate-400">{taskStatusText(task)}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

function StageRow({ stage }: { stage: RunStage }) {
  return (
    <div className="flex items-start gap-2">
      <span className="mt-0.5 shrink-0"><StatusIcon status={stage.status} /></span>
      <div className="min-w-0 flex-1">
        <div className="text-xs font-semibold text-slate-700">
          {stage.label}
          {stage.detail && <span className="ml-1.5 text-[10px] font-normal text-slate-400">{stage.detail}</span>}
        </div>
        {stage.tasks && stage.tasks.length > 0 && <TaskChecklist tasks={stage.tasks} />}
      </div>
    </div>
  );
}

/** 执行中的气泡内容：计时头 + 思考流 + 阶段时间线（含任务清单）。 */
export function ChatRunTimeline({ events }: { events: DebugEvent[] }) {
  const tasks = useMemo(() => buildTasks(events), [events]);
  const stages = useMemo(() => deriveRunStages(events, tasks), [events, tasks]);
  const sections = useMemo(() => deriveThinkingSections(events), [events]);
  const mountedAt = useRef(Date.now());
  const [, setTick] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => setTick((value) => value + 1), 1000);
    return () => clearInterval(timer);
  }, []);

  const firstEventMs = events.length ? Date.parse(events[0].receivedAt) : NaN;
  const startedAtMs = Number.isFinite(firstEventMs) ? firstEventMs : mountedAt.current;
  const elapsedSeconds = Math.max(0, Math.round((Date.now() - startedAtMs) / 1000));

  return (
    <div className="space-y-2.5 text-left">
      <div className="flex items-center gap-1.5 text-[11px] font-bold text-blue-600">
        <Loader2 className="size-3.5 animate-spin" />
        正在执行 · {elapsedSeconds}s
      </div>
      {/* 阶段时间线（含任务清单）在上：一眼看到执行到哪了；思考流是二级信息，折叠放在下面。 */}
      {stages.length > 0 && (
        <div className="space-y-2">
          {stages.map((stage) => <StageRow key={stage.key} stage={stage} />)}
        </div>
      )}
      {sections.length > 0 && <ThinkingSections sections={sections} />}
      {sections.length === 0 && stages.length === 0 && (
        <div className="text-xs text-slate-400">正在思考…</div>
      )}
    </div>
  );
}

/** 终态摘要 chip：completed/失败/等待确认三态，可带「查看执行过程」入口。 */
export function ChatRunSummaryChip({
  status,
  elapsedSeconds,
  traceLabel,
  onOpenTrace,
}: {
  status?: string;
  elapsedSeconds?: number;
  traceLabel?: string;
  onOpenTrace?: () => void;
}) {
  const waiting = status === "waiting_confirmation";
  const failed = status === "failed" || status === "timeout" || status === "cancelled" || status === "stream_failed";
  const tone = waiting
    ? "border-amber-200 bg-amber-50 text-amber-700"
    : failed
      ? "border-rose-100 bg-rose-50 text-rose-600"
      : "border-slate-200 bg-white text-slate-500";
  const icon = waiting
    ? <AlertCircle className="size-3.5 shrink-0" />
    : failed
      ? <XCircle className="size-3.5 shrink-0" />
      : <CheckCircle2 className="size-3.5 shrink-0 text-emerald-500" />;
  const text = waiting
    ? "已暂停，等待你的确认"
    : failed
      ? "执行失败"
      : "已完成";
  const elapsed = typeof elapsedSeconds === "number" && elapsedSeconds > 0
    ? ` · 用时 ${formatDuration(elapsedSeconds * 1000)}`
    : "";
  const className = `mt-2 inline-flex max-w-full items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-[11px] font-semibold ${tone} ${onOpenTrace ? "cursor-pointer transition-colors hover:border-blue-300 hover:text-blue-700" : ""}`;
  const content = (
    <>
      {icon}
      <span className="truncate">{text}{elapsed}</span>
      {onOpenTrace && traceLabel && (
        <span className="inline-flex shrink-0 items-center gap-0.5 text-blue-600">
          · {traceLabel}
          <ChevronRight className="size-3" />
        </span>
      )}
    </>
  );
  return onOpenTrace
    ? <button type="button" onClick={onOpenTrace} className={className}>{content}</button>
    : <div className={className}>{content}</div>;
}
