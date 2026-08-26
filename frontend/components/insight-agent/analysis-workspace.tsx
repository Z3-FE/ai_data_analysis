"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import * as echarts from "echarts/core";
import type { EChartsOption, SeriesOption } from "echarts/types/dist/shared";
import { BarChart, LineChart } from "echarts/charts";
import {
  AriaComponent,
  DataZoomInsideComponent,
  DataZoomSliderComponent,
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import {
  AlertCircle,
  BarChart3,
  Braces,
  CheckCircle2,
  ChevronRight,
  CircleHelp,
  ClipboardList,
  FileBarChart,
  Layers3,
  Loader2,
  MessageSquare,
  Play,
  RotateCcw,
  Send,
  Table2,
  Timer,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

echarts.use([
  AriaComponent,
  BarChart,
  CanvasRenderer,
  DataZoomInsideComponent,
  DataZoomSliderComponent,
  GridComponent,
  LegendComponent,
  LineChart,
  TooltipComponent,
]);

type JsonValue = string | number | boolean | null | Record<string, unknown> | JsonValue[];
type Row = Record<string, JsonValue>;

interface ReportColumn {
  result_name: string;
  display_name?: string;
  field_role?: "dimension" | "metric" | "derived_metric" | "unknown";
  unit?: string | null;
}

interface ReportComponent {
  component_id: string;
  component_type: "text" | "kpi" | "table" | "chart";
  chart_type?: "line" | "bar" | null;
  title: string;
  content: string;
  source_task_id: string;
  dimension_field: string;
  metric_fields: string[];
  value_field: string;
  presentation?: {
    orientation?: "horizontal" | "vertical";
    sort?: "asc" | "desc" | "none";
    top_n?: number | null;
    show_labels?: boolean;
    show_legend?: boolean;
    color_scheme?: string;
  };
  span?: number;
  binding_status: "bound" | "failed";
  binding_error?: string;
  value: JsonValue;
  columns: ReportColumn[];
  data: Row[];
  row_count: number;
  truncated: boolean;
}

interface ReportSection {
  title: string;
  layout: {
    type: "stack" | "grid";
    columns: number;
  };
  components: ReportComponent[];
}

interface RenderedReport {
  status: "success" | "partial" | "failed";
  title: string;
  summary: string;
  sections: ReportSection[];
  limitations: string[];
}

type TaskStatus = "pending" | "running" | "success" | "partial" | "failed";

interface TaskSummary {
  task_id: string;
  status: TaskStatus;
  question?: string;
  resolved_question?: string;
  purpose?: string;
  depends_on: string[];
  phase?: string;
  node?: string;
  error?: string;
  events: DebugEvent[];
}

interface StepReturnGroup {
  step: string;
  status: RunStep["status"];
  latest: DebugEvent;
  events: DebugEvent[];
  taskGroups: TaskReturnGroup[];
  nodeGroups: NodeReturnGroup[];
}

interface TaskReturnGroup {
  taskId: string;
  status: TaskStatus;
  latest?: DebugEvent;
  task?: TaskSummary;
  events: DebugEvent[];
  steps: TaskReturnStep[];
  displayGroups: DisplayEventGroup[];
  nodeGroups: NodeReturnGroup[];
}

interface TaskReturnStep {
  step: string;
  status: RunStep["status"];
  latest: DebugEvent;
  events: DebugEvent[];
  displayGroups: DisplayEventGroup[];
  nodeGroups: NodeReturnGroup[];
}

interface NodeReturnGroup {
  node: string;
  step: string;
  status: RunStep["status"];
  latest: DebugEvent;
  events: DebugEvent[];
  displayGroups: DisplayEventGroup[];
}

interface DisplayEventGroup {
  key: string;
  label: string;
  event: DebugEvent;
  count: number;
}

interface RunStep {
  key: string;
  label: string;
  status: "running" | "success" | "partial" | "failed";
  detail?: string;
}

interface StreamEvent {
  type: string;
  step: string;
  node: string;
  status?: string;
  message?: string;
  rendered_report?: RenderedReport;
  report_plan?: Record<string, unknown>;
  chunk?: string;
  [key: string]: unknown;
}

interface DebugEvent {
  key: string;
  type: string;
  step: string;
  sourceStep: string;
  node: string;
  taskId?: string;
  status?: string;
  receivedAt: string;
  payload: Record<string, unknown>;
  truncatedFields?: string[];
}

const DEFAULT_QUESTION = "找出2017年销售额下降最明显的月份，并分析该月份下降最多的商品类别和卖家地区。";
// 页面渲染调试阶段直接使用固定报告，不请求后端，也不触发 LLM。
const FIXED_REPORT_MODE = true;
const FIXED_RENDERED_REPORT: RenderedReport = {
  status: "success",
  title: "2017年销售额下降最明显月份及归因分析报告",
  summary: "2017年12月是全年销售额下降最明显的月份，环比下降26.36%。其中，“床上浴室与餐桌用品”类别和“圣保罗州”地区的销售额下降最为显著，分别环比下降43.51%和23.51%。",
  sections: [
    {
      title: "2017年销售额整体趋势与下降最明显月份",
      layout: { type: "stack", columns: 1 },
      components: [
        {
          component_id: "月份趋势分析",
          component_type: "text",
          chart_type: null,
          title: "月份趋势分析",
          content: "2017年销售额在12月出现显著下滑。与11月相比，12月总销售额下降了266,357.20，环比降幅达26.36%，是2017年下降最明显的月份。",
          source_task_id: "",
          dimension_field: "",
          metric_fields: [],
          value_field: "",
          presentation: { orientation: "vertical", sort: "none", top_n: null, show_labels: true, show_legend: false, color_scheme: "blue" },
          span: 1,
          binding_status: "bound",
          binding_error: "",
          value: null,
          columns: [],
          data: [],
          row_count: 0,
          truncated: false,
        },
        {
          component_id: "12月销售额下降金额",
          component_type: "kpi",
          chart_type: null,
          title: "12月销售额下降金额",
          content: "",
          source_task_id: "monthly_sales_2016_2017",
          dimension_field: "",
          metric_fields: [],
          value_field: "decrease_amount",
          presentation: { orientation: "vertical", sort: "none", top_n: null, show_labels: true, show_legend: false, color_scheme: "blue" },
          span: 1,
          binding_status: "bound",
          binding_error: "",
          value: 266357.2,
          columns: [],
          data: [],
          row_count: 0,
          truncated: false,
        },
        {
          component_id: "12月销售额环比变化率",
          component_type: "kpi",
          chart_type: null,
          title: "12月销售额环比变化率",
          content: "",
          source_task_id: "monthly_sales_2016_2017",
          dimension_field: "",
          metric_fields: [],
          value_field: "change_rate",
          presentation: { orientation: "vertical", sort: "none", top_n: null, show_labels: true, show_legend: false, color_scheme: "blue" },
          span: 1,
          binding_status: "bound",
          binding_error: "",
          value: -0.263649,
          columns: [],
          data: [],
          row_count: 0,
          truncated: false,
        },
        {
          component_id: "2016年12月至2017年12月销售额趋势",
          component_type: "chart",
          chart_type: "line",
          title: "2016年12月至2017年12月销售额趋势",
          content: "",
          source_task_id: "monthly_sales_2016_2017",
          dimension_field: "year_month_value",
          metric_fields: ["gmv"],
          value_field: "",
          presentation: { orientation: "vertical", sort: "asc", top_n: null, show_labels: true, show_legend: false, color_scheme: "blue" },
          span: 1,
          binding_status: "bound",
          binding_error: "",
          value: null,
          columns: [
            { result_name: "year_month_value", display_name: "下单月份", field_role: "dimension", unit: null },
            { result_name: "gmv", display_name: "销售额", field_role: "metric", unit: "currency" },
          ],
          data: [
            { year_month_value: "2016-12", gmv: "10.90" },
            { year_month_value: "2017-01", gmv: "120312.87" },
            { year_month_value: "2017-02", gmv: "247303.02" },
            { year_month_value: "2017-03", gmv: "374344.30" },
            { year_month_value: "2017-04", gmv: "359927.23" },
            { year_month_value: "2017-05", gmv: "506071.14" },
            { year_month_value: "2017-06", gmv: "433038.60" },
            { year_month_value: "2017-07", gmv: "498031.48" },
            { year_month_value: "2017-08", gmv: "573971.68" },
            { year_month_value: "2017-09", gmv: "624401.69" },
            { year_month_value: "2017-10", gmv: "664219.43" },
            { year_month_value: "2017-11", gmv: "1010271.37" },
            { year_month_value: "2017-12", gmv: "743914.17" },
          ],
          row_count: 13,
          truncated: false,
        },
      ],
    },
    {
      title: "12月销售额下降最多的商品类别",
      layout: { type: "stack", columns: 1 },
      components: [
        {
          component_id: "商品类别分析",
          component_type: "text",
          chart_type: null,
          title: "商品类别分析",
          content: "在2017年12月，销售额下降最多的商品类别是“床上浴室与餐桌用品”。该类别销售额从11月的89,412.54下降至12月的50,505.85，环比下降43.51%，下降金额达38,906.69。",
          source_task_id: "",
          dimension_field: "",
          metric_fields: [],
          value_field: "",
          presentation: { orientation: "vertical", sort: "none", top_n: null, show_labels: true, show_legend: false, color_scheme: "blue" },
          span: 1,
          binding_status: "bound",
          binding_error: "",
          value: null,
          columns: [],
          data: [],
          row_count: 0,
          truncated: false,
        },
        {
          component_id: "该类别下降金额",
          component_type: "kpi",
          chart_type: null,
          title: "该类别下降金额",
          content: "",
          source_task_id: "category_sales_target_month",
          dimension_field: "",
          metric_fields: [],
          value_field: "decrease_amount",
          presentation: { orientation: "vertical", sort: "none", top_n: null, show_labels: true, show_legend: false, color_scheme: "blue" },
          span: 1,
          binding_status: "bound",
          binding_error: "",
          value: 38906.69,
          columns: [],
          data: [],
          row_count: 0,
          truncated: false,
        },
        {
          component_id: "该类别环比变化率",
          component_type: "kpi",
          chart_type: null,
          title: "该类别环比变化率",
          content: "",
          source_task_id: "category_sales_target_month",
          dimension_field: "",
          metric_fields: [],
          value_field: "change_rate",
          presentation: { orientation: "vertical", sort: "none", top_n: null, show_labels: true, show_legend: false, color_scheme: "blue" },
          span: 1,
          binding_status: "bound",
          binding_error: "",
          value: -0.435137,
          columns: [],
          data: [],
          row_count: 0,
          truncated: false,
        },
      ],
    },
    {
      title: "12月销售额下降最多的卖家地区",
      layout: { type: "stack", columns: 1 },
      components: [
        {
          component_id: "卖家地区分析",
          component_type: "text",
          chart_type: null,
          title: "卖家地区分析",
          content: "从卖家地区来看，圣保罗州在12月的销售额下降最为明显。其销售额从11月的622,301.92降至12月的475,972.40，环比下降23.51%，下降金额为146,329.52。",
          source_task_id: "",
          dimension_field: "",
          metric_fields: [],
          value_field: "",
          presentation: { orientation: "vertical", sort: "none", top_n: null, show_labels: true, show_legend: false, color_scheme: "blue" },
          span: 1,
          binding_status: "bound",
          binding_error: "",
          value: null,
          columns: [],
          data: [],
          row_count: 0,
          truncated: false,
        },
        {
          component_id: "该地区下降金额",
          component_type: "kpi",
          chart_type: null,
          title: "该地区下降金额",
          content: "",
          source_task_id: "region_sales_target_month",
          dimension_field: "",
          metric_fields: [],
          value_field: "decrease_amount",
          presentation: { orientation: "vertical", sort: "none", top_n: null, show_labels: true, show_legend: false, color_scheme: "blue" },
          span: 1,
          binding_status: "bound",
          binding_error: "",
          value: 146329.52,
          columns: [],
          data: [],
          row_count: 0,
          truncated: false,
        },
        {
          component_id: "该地区环比变化率",
          component_type: "kpi",
          chart_type: null,
          title: "该地区环比变化率",
          content: "",
          source_task_id: "region_sales_target_month",
          dimension_field: "",
          metric_fields: [],
          value_field: "change_rate",
          presentation: { orientation: "vertical", sort: "none", top_n: null, show_labels: true, show_legend: false, color_scheme: "blue" },
          span: 1,
          binding_status: "bound",
          binding_error: "",
          value: -0.235142,
          columns: [],
          data: [],
          row_count: 0,
          truncated: false,
        },
      ],
    },
  ],
  limitations: [
    "分析仅基于销售额的环比变化，未考虑订单量、客单价或退货率等其他业务指标的影响。",
    "商品类别和卖家地区的分析仅针对下降最明显的月份（2017年12月）进行，未涵盖全年其他月份的波动情况。",
  ],
};
const DEBUG_ROW_LIMIT = 1000;
const DEBUG_ARRAY_FIELDS = new Set(["rows", "data", "display_sql_result", "preview_rows"]);

const DEBUG_STEP_ORDER = [
  "判断问题路由",
  "判断问题路由结果",
  "生成分析计划",
  "提取关键词",
  "召回字段",
  "召回表",
  "召回指标",
  "召回维度值",
  "合并召回信息",
  "过滤指标",
  "过滤表",
  "整理 SQL 上下文",
  "生成 SQL",
  "执行 SQL",
  "增强查询结果",
  "执行分析任务",
  "汇总全部分析证据",
  "生成报告规划",
  "渲染最终报告",
];

const STREAM_EVENT_TYPES = new Set(["reasoning_chunk", "llm_chunk"]);

function mainStepName(sourceStep: string, type = "") {
  // 后端允许更细的节点名，前端将其归入用户约定的 17 个主步骤。
  if (sourceStep === "问题路由" || sourceStep === "判断问题路由") return "判断问题路由";
  if (sourceStep === "抽取关键词" || sourceStep === "提取关键词") return "提取关键词";
  if (sourceStep.startsWith("召回columns")) return "召回字段";
  if (sourceStep.startsWith("召回tables")) return "召回表";
  if (sourceStep.startsWith("召回metrics")) return "召回指标";
  if (sourceStep.startsWith("召回dimension_values")) return "召回维度值";
  if (sourceStep.startsWith("过滤指标")) return "过滤指标";
  if (sourceStep.startsWith("过滤表")) return "过滤表";
  if (sourceStep.startsWith("补全过滤后的上下文") || sourceStep.startsWith("补充 SQL 生成上下文")) return "整理 SQL 上下文";
  if (sourceStep.startsWith("执行分析任务") || type === "analysis_task_result" || type === "analysis_task_resolved") return "执行分析任务";
  if (sourceStep === "生成最终报告") return "渲染最终报告";
  return sourceStep;
}

function stepReturnGroupName(event: DebugEvent) {
  // 路由过程和路由最终结果属于全局编排阶段，单独展示；其余带 task_id 的事件由任务层接管。
  if (!event.taskId && event.type === "question_route") return "判断问题路由结果";
  return event.step;
}

function eventTaskId(event: StreamEvent) {
  // 分析任务事件必须由后端显式提供 task_id。
  const taskId = event.task_id;
  if (typeof taskId === "string" || typeof taskId === "number") return String(taskId);
  return undefined;
}

function debugPayload(value: unknown, truncatedFields: string[], path = ""): unknown {
  // 调试面板只保留大数组的前 1000 条，避免浏览器保存完整查询结果。
  if (Array.isArray(value)) {
    const pathParts = path.split(".");
    const fieldName = pathParts[pathParts.length - 1] || "";
    if (path && DEBUG_ARRAY_FIELDS.has(fieldName) && value.length > DEBUG_ROW_LIMIT) {
      truncatedFields.push(`${path}: ${value.length} -> ${DEBUG_ROW_LIMIT}`);
      return value.slice(0, DEBUG_ROW_LIMIT).map((item, index) => debugPayload(item, truncatedFields, `${path}[${index}]`));
    }
    return value.map((item, index) => debugPayload(item, truncatedFields, `${path}[${index}]`));
  }
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => {
        const childPath = path ? `${path}.${key}` : key;
        return [key, debugPayload(item, truncatedFields, childPath)];
      }),
    );
  }
  return value;
}

function makeDebugEvent(event: StreamEvent): DebugEvent {
  // 用 type、step、node、phase 和 task_id 合并同一节点的流式片段。
  if (typeof event.type !== "string" || !event.type.trim()) {
    throw new Error("SSE 事件缺少必需的 type 字段。");
  }
  if (typeof event.step !== "string" || !event.step.trim()) {
    throw new Error("SSE 事件缺少必需的 step 字段。");
  }
  if (typeof event.node !== "string" || !event.node.trim()) {
    throw new Error("SSE 事件 " + event.type + " 缺少必需的 node 字段。");
  }
  const type = event.type.trim();
  const sourceStep = event.step.trim();
  const step = mainStepName(sourceStep, type);
  const node = event.node.trim();
  const taskId = eventTaskId(event);
  const phase = typeof event.phase === "string" ? event.phase : "";
  const truncatedFields: string[] = [];
  const payload = debugPayload(event, truncatedFields) as Record<string, unknown>;
  if (truncatedFields.length) {
    payload.debug_truncated = true;
    payload.debug_truncated_fields = truncatedFields;
  }
  return {
    key: `${type}:${sourceStep}:${node || ""}:${phase}:${taskId || ""}`,
    type,
    step,
    sourceStep,
    node,
    taskId,
    status: typeof event.status === "string" ? event.status : undefined,
    receivedAt: new Date().toISOString(),
    payload,
    truncatedFields: truncatedFields.length ? truncatedFields : undefined,
  };
}

function upsertDebugEvent(events: DebugEvent[], event: StreamEvent) {
  const nextEvent = makeDebugEvent(event);
  const index = events.findIndex((item) => item.key === nextEvent.key);
  if (index === -1) return [...events, nextEvent];
  const next = [...events];
  const previous = next[index];
  if (["reasoning_chunk", "llm_chunk"].includes(nextEvent.type)) {
    const previousChunk = typeof previous.payload.chunk === "string" ? previous.payload.chunk : "";
    const currentChunk = typeof nextEvent.payload.chunk === "string" ? nextEvent.payload.chunk : "";
    next[index] = {
      ...nextEvent,
      payload: { ...nextEvent.payload, chunk: previousChunk + currentChunk },
      truncatedFields: [...(previous.truncatedFields || []), ...(nextEvent.truncatedFields || [])],
    };
  } else {
    next[index] = nextEvent;
  }
  return next;
}

function isTerminalStatus(status: string | undefined) {
  return status === "success" || status === "partial" || status === "failed";
}

function isTerminalEvent(event: DebugEvent) {
  return event.type === "error" || isTerminalStatus(event.status);
}

function displayEventIdentity(event: DebugEvent) {
  // 只有同一任务、同一步骤、同一 Agent 节点的 running 才会被对应终态覆盖。
  return event.step + "::" + (event.taskId || "__main_step__") + "::" + event.node;
}

function collapseSupersededRunningEvents(events: DebugEvent[], finalStatus?: string) {
  // 步骤返回和分析任务只展示当前状态；全部事件仍使用原始 debugEvents，不经过这里。
  // 分析任务最终结果是任务级终态，阶段事件没有独立终态时也要随任务结束。
  const terminalIdentities = new Set(
    events.filter(isTerminalEvent).map(displayEventIdentity),
  );
  return events.filter((event) => {
    if (event.status !== "running") return true;
    if (isTerminalStatus(finalStatus)) return false;
    return !terminalIdentities.has(displayEventIdentity(event));
  });
}

function aggregateStatus(statuses: Array<string | undefined>): RunStep["status"] {
  const normalized = statuses.filter(Boolean);
  if (normalized.includes("failed")) {
    return normalized.includes("success") || normalized.includes("partial") ? "partial" : "failed";
  }
  if (normalized.includes("partial")) return "partial";
  if (normalized.includes("success")) return "success";
  if (normalized.includes("running") || normalized.includes("pending")) return "running";
  return "running";
}

function resolveTaskStepStatus(
  events: DebugEvent[],
  taskStatusValue: TaskStatus,
): RunStep["status"] {
  // 阶段事件通常只有 running，最终分析任务结果才携带 success/failed；
  // 任务已经结束时，将没有独立终态事件的阶段同步为任务最终状态。
  const explicitStatuses = events.map((event) => event.type === "error" ? "failed" : event.status);
  if (explicitStatuses.some((status) => isTerminalStatus(status))) {
    return aggregateStatus(explicitStatuses);
  }
  if (isTerminalStatus(taskStatusValue)) return taskStatusValue;
  return aggregateStatus(explicitStatuses);
}

function aggregateTaskStatus(tasks: TaskSummary[]): RunStep["status"] {
  if (tasks.some((task) => task.status === "pending" || task.status === "running")) return "running";
  const statuses = tasks.map((task) => task.status);
  if (statuses.includes("failed")) return statuses.includes("success") || statuses.includes("partial") ? "partial" : "failed";
  if (statuses.includes("partial")) return "partial";
  return statuses.length && statuses.every((status) => status === "success") ? "success" : "running";
}

function taskStatus(events: DebugEvent[]): TaskStatus {
  const result = events.find((event) => event.type === "analysis_task_result");
  if (result?.status === "failed" || events.some((event) => event.type === "error")) return "failed";
  if (result?.status === "partial") return "partial";
  if (result?.status === "success") return "success";
  return events.length ? "running" : "pending";
}

function buildTaskSummaries(events: DebugEvent[]): TaskSummary[] {
  const plannedTasks = events.flatMap((event) => {
    if (event.type !== "analysis_plan" || !Array.isArray(event.payload.tasks)) return [];
    return event.payload.tasks.filter((task): task is Record<string, unknown> => Boolean(task && typeof task === "object"));
  });
  const taskIds = new Set<string>();
  const summaries = new Map<string, TaskSummary>();

  for (const task of plannedTasks) {
    const taskId = typeof task.task_id === "string" ? task.task_id : "";
    if (!taskId || taskIds.has(taskId)) continue;
    taskIds.add(taskId);
    summaries.set(taskId, {
      task_id: taskId,
      status: "pending",
      question: typeof task.question === "string" ? task.question : undefined,
      purpose: typeof task.purpose === "string" ? task.purpose : undefined,
      depends_on: Array.isArray(task.depends_on) ? task.depends_on.filter((item): item is string => typeof item === "string") : [],
      events: [],
    });
  }

  for (const event of events) {
    if (!event.taskId) continue;
    const existing = summaries.get(event.taskId) || {
      task_id: event.taskId,
      status: "pending" as TaskStatus,
      depends_on: [],
      events: [],
    };
    existing.events = [...existing.events, event];
    if (typeof event.payload.question === "string") existing.question = event.payload.question;
    if (typeof event.payload.resolved_question === "string") existing.resolved_question = event.payload.resolved_question;
    if (typeof event.payload.purpose === "string") existing.purpose = event.payload.purpose;
    if (event.node) existing.node = event.node;
    if (Array.isArray(event.payload.depends_on)) existing.depends_on = event.payload.depends_on.filter((item): item is string => typeof item === "string");
    if (typeof event.payload.error === "string") existing.error = event.payload.error;
    if (event.type === "analysis_task_phase" && typeof event.payload.phase === "string") existing.phase = event.payload.phase;
    summaries.set(event.taskId, existing);
  }

  return Array.from(summaries.values()).map((summary) => {
    const status = taskStatus(summary.events);
    return {
      ...summary,
      events: collapseSupersededRunningEvents(summary.events, status),
      status,
    };
  });
}

function buildProgressSteps(events: DebugEvent[], tasks: TaskSummary[]): RunStep[] {
  const grouped = new Map<string, DebugEvent[]>();
  for (const event of events) {
    const current = grouped.get(event.step) || [];
    grouped.set(event.step, [...current, event]);
  }
  return Array.from(grouped.entries())
    .map(([step, stepEvents]) => {
      const status = step === "执行分析任务"
        ? aggregateTaskStatus(tasks)
        : aggregateStatus(stepEvents.map((event) => event.type === "error" ? "failed" : event.status));
      const latest = stepEvents[stepEvents.length - 1];
      const completedTasks = tasks.filter((task) => isTerminalStatus(task.status)).length;
      return {
        key: step,
        label: step,
        status,
        detail: step === "执行分析任务" && tasks.length
          ? "已完成 " + completedTasks + " / " + tasks.length + " 个分析任务"
          : typeof latest.payload.message === "string"
            ? latest.payload.message
            : typeof latest.payload.error === "string" ? latest.payload.error : undefined,
      };
    })
    .sort((left, right) => DEBUG_STEP_ORDER.indexOf(left.key) - DEBUG_STEP_ORDER.indexOf(right.key));
}

function buildStepReturnGroups(events: DebugEvent[], tasks: TaskSummary[]): StepReturnGroup[] {
  const grouped = new Map<string, DebugEvent[]>();
  for (const event of events.filter((item) => !STREAM_EVENT_TYPES.has(item.type))) {
    if (event.taskId) continue;
    const groupName = stepReturnGroupName(event);
    const current = grouped.get(groupName) || [];
    grouped.set(groupName, [...current, event]);
  }
  // 路由结果使用独立的展示名称；结果到达后隐藏前面的路由 running 卡片。
  const routeResultEvents = grouped.get("判断问题路由结果");
  if (routeResultEvents?.some(isTerminalEvent)) {
    grouped.delete("判断问题路由");
  }
  return Array.from(grouped.entries())
    .map(([step, stepEvents]) => {
      const displayEvents = collapseSupersededRunningEvents(stepEvents);
      const latest = displayEvents[displayEvents.length - 1] || stepEvents[stepEvents.length - 1];
      return {
        step,
        status: aggregateStatus(displayEvents.map((event) => event.type === "error" ? "failed" : event.status)),
        latest,
        events: displayEvents,
        taskGroups: [],
        nodeGroups: buildNodeReturnGroups(displayEvents),
      };
    })
    .concat(tasks.length ? [{
      step: "执行分析任务",
      status: aggregateTaskStatus(tasks),
      latest: taskStepOverviewEvent(tasks),
      events: [],
      taskGroups: buildTaskReturnGroups(tasks),
      nodeGroups: [],
    }] as any : [])
    .sort((left, right) => DEBUG_STEP_ORDER.indexOf(left.step) - DEBUG_STEP_ORDER.indexOf(right.step));
}

function buildNodeReturnGroups(events: DebugEvent[], finalStatus?: TaskStatus): NodeReturnGroup[] {
  // 节点层只按真实 Agent 节点归并；事件类型仍作为节点内部的返回内容展示。
  const grouped = new Map<string, DebugEvent[]>();
  for (const event of events) {
    const node = event.node;
    const current = grouped.get(node) || [];
    grouped.set(node, [...current, event]);
  }
  return Array.from(grouped.entries())
    .map(([node, nodeEvents]) => {
      const latest = nodeEvents[nodeEvents.length - 1];
      const statuses = nodeEvents.map((event) => event.type === "error" ? "failed" : event.status);
      return {
        node,
        step: latest.step,
        status: statuses.some((status) => isTerminalStatus(status))
          ? aggregateStatus(statuses)
          : isTerminalStatus(finalStatus) ? finalStatus : aggregateStatus(statuses),
        latest,
        events: nodeEvents,
        displayGroups: buildDisplayEventGroups(nodeEvents),
      };
    })
    .sort((left, right) => {
      const leftIndex = DEBUG_STEP_ORDER.indexOf(left.step);
      const rightIndex = DEBUG_STEP_ORDER.indexOf(right.step);
      return (leftIndex === -1 ? DEBUG_STEP_ORDER.length : leftIndex) - (rightIndex === -1 ? DEBUG_STEP_ORDER.length : rightIndex);
    });
}

function debugJson(value: unknown) {
  // SSE 已经完成 JSON 解析，这里只负责安全、可读地展示调试副本。
  const serialized = JSON.stringify(value, null, 2);
  if (serialized === undefined) return String(value);
  // JSON.stringify 会把字符串中的控制字符显示成字面量 \\n；调试面板中还原为真正换行便于阅读。
  return serialized.replace(/\\+r\\+n|\\+n|\\+r|\\+t/g, (escaped) => {
    if (escaped.endsWith("t")) return "\t";
    return "\n";
  });
}

function debugEventLabel(event: DebugEvent) {
  const parts = [event.type, event.status].filter(Boolean);
  if (event.taskId) parts.push(`任务：${event.taskId}`);
  if (event.node) parts.push(`节点：${event.node}`);
  return parts.join(" · ");
}

function displayEventLabel(event: DebugEvent) {
  // 展示视图使用业务化名称，内部事件类型只作为副标题保留。
  const phase = typeof event.payload.phase === "string" ? event.payload.phase : "";
  const withPhase = (label: string) => phase ? label + " · " + phase : label;
  if (event.type === "reasoning_result") return withPhase("思考过程");
  if (event.type === "reasoning_chunk") return withPhase("思考过程片段");
  if (event.type === "llm_result") return withPhase("模型原始输出");
  if (event.type === "llm_chunk") return withPhase("模型输出片段");
  if (event.type === "analysis_task_phase") return withPhase("分析阶段");
  if (event.type === "analysis_task_result") return "分析任务结果";
  if (event.type === "analysis_task_resolved") return "依赖结果解析";
  if (event.type === "error") return "错误信息";
  if (event.type === "report_plan_result") return "报告规划结果";
  if (event.type === "rendered_report") return "最终报告";
  if (event.status === "success") return event.step + "结果";
  if (event.status === "failed") return event.step + "失败信息";
  if (event.status === "partial") return event.step + "部分结果";
  if (event.status === "running") return "开始执行";
  return event.step + "返回";
}

function taskReturnStepName(event: DebugEvent) {
  // 执行分析任务本身只是外层包装；任务内部展示具体查询、SQL 和计算阶段。
  if (event.type === "analysis_task_result") return "分析任务结果";
  if (event.type === "analysis_task_resolved") return "依赖结果解析";
  if (event.type === "analysis_task_phase") {
    return typeof event.payload.phase === "string" ? event.payload.phase : "分析阶段";
  }
  return event.step;
}

function buildDisplayEventGroups(events: DebugEvent[]) {
  // 业务视图合并同一节点的内部返回；全部事件视图不调用此函数。
  const hasReasoningResult = events.some((event) => event.type === "reasoning_result");
  const hasLlmResult = events.some((event) => event.type === "llm_result");
  const hasNodeResult = events.some((event) => event.type !== "progress");
  const groups = new Map<string, DisplayEventGroup>();

  for (const event of events) {
    if (hasNodeResult && event.type === "progress") continue;
    if (hasReasoningResult && event.type === "reasoning_chunk") continue;
    if (hasLlmResult && event.type === "llm_chunk") continue;
    const label = displayEventLabel(event);
    const current = groups.get(label);
    groups.set(label, current
      ? { ...current, event, count: current.count + 1 }
      : { key: event.key + ":display", label, event, count: 1 });
  }
  return Array.from(groups.values());
}

function buildTaskReturnGroups(tasks: TaskSummary[]) {
  // 步骤返回需要保留每个 task_id 的完整上下文；不能把不同任务的同名信息项合并。
  return tasks.map((task) => {
    const events = collapseSupersededRunningEvents(task.events, task.status);
    const grouped = new Map<string, DebugEvent[]>();
    const stepEvents = events.filter((event) => event.type !== "progress");
    for (const event of stepEvents) {
      const step = taskReturnStepName(event);
      const current = grouped.get(step) || [];
      grouped.set(step, [...current, event]);
    }
    const steps = Array.from(grouped.entries())
      .map(([step, stepEvents]) => {
        const latest = stepEvents[stepEvents.length - 1];
        return {
          step,
          status: resolveTaskStepStatus(stepEvents, task.status),
          latest,
          events: stepEvents,
          displayGroups: buildDisplayEventGroups(stepEvents),
          nodeGroups: buildNodeReturnGroups(stepEvents, task.status),
        };
      })
      .sort((left, right) => {
        const leftIndex = DEBUG_STEP_ORDER.indexOf(left.step);
        const rightIndex = DEBUG_STEP_ORDER.indexOf(right.step);
        return (leftIndex === -1 ? DEBUG_STEP_ORDER.length : leftIndex) - (rightIndex === -1 ? DEBUG_STEP_ORDER.length : rightIndex);
      });
    return {
      taskId: task.task_id,
      status: task.status,
      latest: events[events.length - 1],
      task,
      events,
      steps,
      displayGroups: buildDisplayEventGroups(events),
      nodeGroups: buildNodeReturnGroups(events, task.status),
    };
  });
}

function taskStepOverviewEvent(tasks: TaskSummary[]): DebugEvent {
  // 分析计划已经生成但任务尚未发出第一条事件时，先给步骤返回提供可见的占位节点。
  const status = aggregateTaskStatus(tasks);
  return {
    key: "analysis-task-overview",
    type: "analysis_task_overview",
    step: "执行分析任务",
    sourceStep: "执行分析任务",
    node: "execute_analysis",
    status,
    receivedAt: new Date().toISOString(),
    payload: {
      type: "analysis_task_overview",
      step: "执行分析任务",
      status,
      task_count: tasks.length,
      message: "分析计划已生成，等待分析任务开始执行。",
    },
  };
}

function orderedDebugEvents(events: DebugEvent[]) {
  return [...events].sort((left, right) => {
    const leftIndex = DEBUG_STEP_ORDER.indexOf(left.step);
    const rightIndex = DEBUG_STEP_ORDER.indexOf(right.step);
    return (leftIndex === -1 ? DEBUG_STEP_ORDER.length : leftIndex) - (rightIndex === -1 ? DEBUG_STEP_ORDER.length : rightIndex);
  });
}

function textValue(value: JsonValue | undefined) {
  // 统一格式化报告中的动态值，不依赖固定字段名或固定数据类型。
  if (value === null || value === undefined) return "--";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function numericValue(value: JsonValue | undefined) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) return parsed;
  }
  return null;
}

function formatNumber(value: number, unit?: string | null) {
  const maximumFractionDigits = unit === "currency" || unit === "percent" ? 2 : 4;
  const formatted = value.toLocaleString("zh-CN", { maximumFractionDigits });
  if (unit === "currency") return `${formatted} 元`;
  if (unit === "percent") return `${(value * 100).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}%`;
  return formatted;
}

function formatAxisValue(value: number, unit?: string | null) {
  // 坐标轴使用紧凑单位，避免完整金额把图表的可视区域挤出卡片。
  if (unit === "percent") {
    return `${(value * 100).toLocaleString("zh-CN", { maximumFractionDigits: 1 })}%`;
  }
  const absolute = Math.abs(value);
  if (unit === "currency" && absolute >= 100000000) {
    return `${(value / 100000000).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} 亿`;
  }
  if (unit === "currency" && absolute >= 10000) {
    return `${(value / 10000).toLocaleString("zh-CN", { maximumFractionDigits: 1 })} 万`;
  }
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 1 });
}

function formatCell(value: JsonValue | undefined, column?: ReportColumn) {
  // 数字字符串也按数值处理，单位优先使用后端字段元数据。
  const number = numericValue(value);
  const isNumericColumn = typeof value === "number" || column?.field_role === "metric" || column?.field_role === "derived_metric" || Boolean(column?.unit);
  if (number !== null && isNumericColumn) return formatNumber(number, column?.unit);
  return textValue(value);
}

function inferMetricUnit(title: string, valueField = "") {
  // 报告协议目前没有强制 KPI 单位，展示层根据标题和结果键补充最小的格式语义。
  const hint = title + " " + valueField;
  if (hint.includes("变化率") || hint.includes("降幅") || hint.includes("率") || valueField === "change_rate") return "percent";
  if (hint.includes("金额") || hint.includes("销售额") || hint.includes("价格") || valueField === "decrease_amount") return "currency";
  return undefined;
}

function metricValue(value: JsonValue | undefined, title: string, valueField?: string) {
  // 计算结果可能是对象。根据组件已有的 value_field 或标题语义取一个标量，
  // 让报告展示层不把完整计算对象当成 KPI 内容打印出来。
  if (value === null || value === undefined) return null;
  if (typeof value !== "object" || Array.isArray(value)) return value;
  const objectValue = value as Record<string, JsonValue>;
  const candidates = [
    valueField,
    title.includes("变化率") || title.includes("降幅") ? "change_rate" : "",
    title.includes("下降金额") ? "decrease_amount" : "",
    title.includes("销售额") ? "target_value" : "",
    "value",
    "result",
  ].filter(Boolean) as string[];
  for (const key of candidates) {
    if (key in objectValue && objectValue[key] !== null && objectValue[key] !== undefined) return objectValue[key];
  }
  const firstScalar = Object.values(objectValue).find((item) => numericValue(item) !== null);
  return firstScalar ?? null;
}

function displayName(column: ReportColumn | undefined, fallback: string) {
  // 后端优先返回业务显示名称，没有映射时再退回原始字段名。
  return column?.display_name || fallback;
}

async function* streamAgent(question: string, sessionId: string, signal: AbortSignal) {
  // 调用固定 SSE 入口，会话 ID 通过参数传递，不进入 URL 路径。
  const response = await fetch(`/api/analysis?session_id=${encodeURIComponent(sessionId)}`, {
    method: "POST",
    cache: "no-store",
    headers: {
      Accept: "text/event-stream",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ session_id: sessionId, input_text: question }),
    signal,
  });
  if (!response.ok || !response.body) {
    throw new Error("分析服务连接失败，请确认后端已启动。");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    for (const block of blocks) {
      const line = block.split("\n").find((item) => item.startsWith("data: "));
      if (line) yield JSON.parse(line.slice(6)) as StreamEvent;
    }
  }
  if (buffer.trim()) {
    const line = buffer.split("\n").find((item) => item.startsWith("data: "));
    if (line) yield JSON.parse(line.slice(6)) as StreamEvent;
  }
}

function StatusBadge({ status }: { status: string }) {
  // 用统一状态标识区分报告生成中的运行、部分完成和失败。
  const config =
    status === "success"
      ? { label: "分析完成", className: "border-emerald-200 bg-emerald-50 text-emerald-700" }
      : status === "partial"
        ? { label: "部分完成", className: "border-amber-200 bg-amber-50 text-amber-700" }
        : status === "failed"
          ? { label: "分析失败", className: "border-rose-200 bg-rose-50 text-rose-700" }
          : { label: "准备分析", className: "border-slate-200 bg-slate-50 text-slate-500" };
  return <Badge variant="outline" className={config.className}>{config.label}</Badge>;
}

function ReportMetric({ component }: { component: ReportComponent }) {
  if (component.binding_status === "failed") return <ReportBindingError component={component} />;
  const value = metricValue(component.value, component.title, component.value_field);
  const number = numericValue(value);
  const unit = inferMetricUnit(component.title, component.value_field);
  return (
    <section className="h-full min-w-0 rounded-lg border border-slate-200/90 bg-white px-5 py-4 shadow-[0_8px_24px_-20px_rgba(15,23,42,0.65)]">
      <div className="min-w-0">
        <h4 className="break-words text-sm font-bold leading-5 text-slate-700">{component.title}</h4>
      </div>
      <div className={"mt-3 break-words text-2xl font-extrabold tracking-tight " + (number !== null && number < 0 ? "text-rose-700" : "text-slate-950")}>
        {number !== null ? formatNumber(number, unit) : textValue(value)}
      </div>
    </section>
  );
}

function DynamicTable({ component }: { component: ReportComponent }) {
  // 只渲染后端绑定到组件的真实行，不假设任何固定业务字段。
  const [expanded, setExpanded] = useState(false);
  if (component.binding_status === "failed") {
    return <ReportBindingError component={component} />;
  }
  const rows = component.data || [];
  const previewRows = expanded ? rows : rows.slice(0, 10);
  return (
    <section className="overflow-hidden rounded-lg border border-slate-200/90 bg-white shadow-[0_8px_24px_-20px_rgba(15,23,42,0.65)]">
      <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <div className="flex min-w-0 items-start gap-2.5"><Table2 className="mt-0.5 size-4 shrink-0 text-teal-700" /><div className="min-w-0"><h3 className="break-words text-sm font-extrabold leading-5 text-slate-800">{component.title}</h3><p className="mt-1 text-[11px] text-slate-400">明细数据</p></div></div>
        <span className="shrink-0 text-[11px] font-semibold text-slate-400">共 {component.row_count} 行</span>
      </div>
      <div className="max-h-[380px] overflow-auto">
        <table className="min-w-full text-left text-xs">
          <thead className="sticky top-0 z-10 bg-slate-50 text-[11px] font-bold text-slate-500 shadow-[0_1px_0_#e2e8f0]"><tr>{component.columns.map((column) => <th key={column.result_name} className="whitespace-nowrap px-5 py-3">{displayName(column, column.result_name)}</th>)}</tr></thead>
          <tbody className="divide-y divide-slate-100">
            {previewRows.map((row, rowIndex) => <tr key={rowIndex} className="hover:bg-slate-50/80">{component.columns.map((column) => <td key={column.result_name} className="whitespace-nowrap px-5 py-2.5 text-slate-700">{formatCell(row[column.result_name], column)}</td>)}</tr>)}
          </tbody>
        </table>
      </div>
      {(rows.length > 10 || component.truncated) && <div className="flex items-center justify-between gap-3 border-t border-slate-100 px-5 py-3 text-[11px] text-slate-400">
        <span>{expanded ? "当前展示 " + rows.length + " 行" : "报告先展示前 " + Math.min(rows.length, 10) + " 行"}{component.truncated ? "，完整结果由后端保留" : ""}</span>
        {rows.length > 10 && <button type="button" onClick={() => setExpanded((current) => !current)} className="inline-flex shrink-0 items-center gap-1 font-bold text-teal-700 hover:text-teal-900">{expanded ? "收起明细" : "展开全部 " + rows.length + " 行"}<ChevronRight className={"size-3.5 transition-transform " + (expanded ? "rotate-90" : "")} /></button>}
      </div>}
    </section>
  );
}

function chartCategoryLabel(value: unknown) {
  const label = String(value ?? "");
  return label.length > 14 ? `${label.slice(0, 14)}…` : label;
}

function ReportChart({ component }: { component: ReportComponent }) {
  // 先确认报告组件已绑定真实字段，再交给 ECharts 组件绘制。
  if (component.binding_status === "failed") {
    return <ReportBindingError component={component} />;
  }
  if (component.chart_type !== "line" && component.chart_type !== "bar") {
    return <ReportBindingError component={{ ...component, binding_error: "图表缺少有效的图表类型。" }} />;
  }
  const dimension = component.columns.find((column) => column.result_name === component.dimension_field);
  const metricColumns = component.metric_fields
    .map((field) => component.columns.find((column) => column.result_name === field))
    .filter((column): column is ReportColumn => Boolean(column));
  if (!dimension || !metricColumns.length || metricColumns.length !== component.metric_fields.length) {
    return <ReportBindingError component={{ ...component, binding_error: "图表缺少有效的维度或指标字段。" }} />;
  }
  if (!component.data.length) {
    return <section className="rounded-lg border border-dashed border-slate-300 bg-slate-50/70 px-5 py-10 text-center text-xs text-slate-400">暂无可展示的图表数据</section>;
  }
  return <EChartsReportChart component={component} dimension={dimension} metricColumns={metricColumns} />;
}

function escapeTooltipText(value: unknown) {
  // tooltip 使用 HTML 字符串，报告中的动态字段不能直接插入 HTML。
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('\"', "&quot;")
    .replaceAll("'", "&#39;");
}

function EChartsReportChart({
  component,
  dimension,
  metricColumns,
}: {
  component: ReportComponent;
  dimension: ReportColumn;
  metricColumns: ReportColumn[];
}) {
  // ECharts 只接收报告协议中的字段和真实行数据，不接收 LLM 生成的 option。
  const chartRef = useRef<HTMLDivElement>(null);
  const chartInstanceRef = useRef<ReturnType<typeof echarts.init> | null>(null);
  const presentation = component.presentation || {};
  const isLine = component.chart_type === "line";
  const isHorizontal = component.chart_type === "bar" && presentation.orientation === "horizontal";
  const metric = metricColumns[0];
  const preparedRows = useMemo(() => {
    const rows = [...component.data];
    if (!isLine && presentation.sort && presentation.sort !== "none") {
      rows.sort((left, right) => {
        const leftValue = numericValue(left[metric.result_name]) ?? 0;
        const rightValue = numericValue(right[metric.result_name]) ?? 0;
        return presentation.sort === "asc" ? leftValue - rightValue : rightValue - leftValue;
      });
    }
    const limit = presentation.top_n ?? (isHorizontal || !isLine ? 12 : 24);
    return rows.slice(0, limit);
  }, [component.data, isHorizontal, isLine, metric.result_name, presentation.sort, presentation.top_n]);
  const chartRowsLabel = preparedRows.length < component.row_count
    ? `展示 ${preparedRows.length} / ${component.row_count} 个数据点`
    : `${component.row_count} 个数据点`;
  const option = useMemo<EChartsOption>(() => {
    const colors = presentation.color_scheme === "teal"
      ? ["#0f766e", "#14b8a6", "#5eead4"]
      : presentation.color_scheme === "amber"
        ? ["#d97706", "#f59e0b", "#fbbf24"]
        : ["#2563eb", "#60a5fa", "#93c5fd"];
    const categories = preparedRows.map((row) => textValue(row[dimension.result_name]));
    const series: SeriesOption[] = metricColumns.map((column) => ({
      type: isLine ? ("line" as const) : ("bar" as const),
      name: displayName(column, column.result_name),
      data: preparedRows.map((row) => numericValue(row[column.result_name])),
      smooth: isLine,
      showSymbol: isLine,
      symbolSize: isLine ? 7 : undefined,
      barMaxWidth: isHorizontal ? 22 : 34,
      barGap: "25%",
      label: {
        show: Boolean(presentation.show_labels),
        formatter: (params: { value?: unknown }) => {
          const number = numericValue(params.value as JsonValue);
          return number === null ? "" : formatNumber(number, column.unit);
        },
      },
      emphasis: { focus: "series" },
    }));
    const tooltipFormatter = (params: unknown) => {
      const items = (Array.isArray(params) ? params : [params]) as Array<{
        seriesName?: string;
        seriesIndex?: number;
        value?: unknown;
        axisValue?: unknown;
      }>;
      const category = items[0]?.axisValue ?? "";
      const lines = items
        .map((item) => {
          const number = numericValue(item.value as JsonValue);
          const seriesColumn = metricColumns[item.seriesIndex ?? 0] || metric;
          return `<div>${escapeTooltipText(item.seriesName || "指标")}：${number === null ? "--" : formatNumber(number, seriesColumn.unit)}</div>`;
        })
        .join("");
      return `<div><strong>${escapeTooltipText(category)}</strong>${lines}</div>`;
    };
    const valueAxis = {
      type: "value" as const,
      axisLabel: { formatter: (value: number) => formatAxisValue(value, metric.unit) },
      splitLine: { lineStyle: { color: "#e2e8f0", type: "dashed" as const } },
    };
    const categoryAxis = {
      type: "category" as const,
      data: categories,
      axisLabel: { formatter: chartCategoryLabel, interval: isLine ? 0 : ("auto" as const) },
      axisTick: { alignWithLabel: true },
    };
    return {
      animationDuration: 350,
      color: colors,
      grid: {
        left: isHorizontal ? 132 : 62,
        right: 24,
        top: presentation.show_legend ? 42 : 20,
        bottom: isLine ? 88 : isHorizontal ? 28 : 52,
        containLabel: true,
      },
      dataZoom: isLine
        ? [
            {
              type: "inside",
              xAxisIndex: 0,
              filterMode: "none",
              start: 0,
              end: 100,
            },
            {
              type: "slider",
              xAxisIndex: 0,
              filterMode: "none",
              start: 0,
              end: 100,
              bottom: 10,
              height: 18,
              showDetail: false,
              borderColor: "#cbd5e1",
              fillerColor: "rgba(37, 99, 235, 0.12)",
              handleStyle: { color: "#2563eb", borderColor: "#2563eb" },
              moveHandleStyle: { color: "#94a3b8" },
              dataBackground: {
                lineStyle: { color: "#94a3b8" },
                areaStyle: { color: "#e2e8f0" },
              },
            },
          ]
        : undefined,
      tooltip: {
        trigger: "axis",
        axisPointer: { type: isLine ? "cross" : "shadow" },
        formatter: tooltipFormatter,
      },
      legend: {
        show: Boolean(presentation.show_legend),
        top: 4,
        type: "scroll",
      },
      xAxis: isHorizontal ? valueAxis : categoryAxis,
      yAxis: isHorizontal
        ? { ...categoryAxis, inverse: true, axisLabel: { formatter: chartCategoryLabel } }
        : valueAxis,
      series,
    };
  }, [dimension.result_name, isHorizontal, isLine, metric.unit, metricColumns, preparedRows, presentation.color_scheme, presentation.show_labels, presentation.show_legend]);
  useEffect(() => {
    if (!chartRef.current) return;
    const chart = echarts.init(chartRef.current, undefined, { renderer: "canvas" });
    chartInstanceRef.current = chart;
    const observer = typeof ResizeObserver === "undefined"
      ? null
      : new ResizeObserver(() => chart.resize());
    observer?.observe(chartRef.current);
    const resize = () => chart.resize();
    window.addEventListener("resize", resize);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", resize);
      chart.dispose();
      chartInstanceRef.current = null;
    };
  }, []);
  useEffect(() => {
    chartInstanceRef.current?.setOption(option, true);
    chartInstanceRef.current?.resize();
  }, [option]);
  const chartHeight = isHorizontal
    ? Math.max(300, Math.min(620, preparedRows.length * 32 + 64))
    : isLine ? 360 : 320;
  return (
    <section className="overflow-hidden rounded-lg border border-slate-200/90 bg-white shadow-[0_8px_24px_-20px_rgba(15,23,42,0.65)]">
      <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4"><div className="flex min-w-0 items-start gap-2.5"><BarChart3 className="mt-0.5 size-4 shrink-0 text-blue-700" /><div className="min-w-0"><h3 className="break-words text-sm font-extrabold leading-5 text-slate-800">{component.title}</h3><p className="mt-1 text-[11px] text-slate-400">{metricColumns.map((column) => displayName(column, column.result_name)).join("、")} · {chartRowsLabel}</p></div></div><span className="shrink-0 text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">{isLine ? "趋势" : "对比"}</span></div>
      <div className="min-w-0 px-3 pb-3 pt-2" style={{ height: chartHeight }}><div ref={chartRef} className="h-full min-w-0 w-full" role="img" aria-label={component.title} /></div>
    </section>
  );
}

function ReportBindingError({ component }: { component: ReportComponent }) {
  return <section className="border border-rose-200 bg-rose-50/70 px-4 py-3"><div className="flex items-center gap-2 text-sm font-bold text-rose-700"><AlertCircle className="size-4" />{component.title}</div><p className="mt-2 text-xs leading-5 text-rose-700">{component.binding_error || "组件数据绑定失败。"}</p></section>;
}

type ReportBlock =
  | { kind: "component"; component: ReportComponent }
  | { kind: "kpi-row"; components: ReportComponent[] };

function groupReportComponents(components: ReportComponent[]): ReportBlock[] {
  // 连续的两个及以上 KPI 组成独立一行，避免每个 KPI 占满整行。
  const blocks: ReportBlock[] = [];
  let kpiGroup: ReportComponent[] = [];
  const flushKpiGroup = () => {
    if (kpiGroup.length >= 2) {
      blocks.push({ kind: "kpi-row", components: kpiGroup });
    } else if (kpiGroup.length === 1) {
      blocks.push({ kind: "component", component: kpiGroup[0] });
    }
    kpiGroup = [];
  };

  for (const component of components) {
    if (component.component_type === "kpi") {
      kpiGroup.push(component);
    } else {
      flushKpiGroup();
      blocks.push({ kind: "component", component });
    }
  }
  flushKpiGroup();
  return blocks;
}

function ReportView({ report }: { report: RenderedReport }) {
  // 由前端统一组织报告层级，LLM 只提供内容和组件意图。
  const renderComponent = (component: ReportComponent) => {
    if (component.binding_status === "failed") return <ReportBindingError key={component.component_id} component={component} />;
    if (component.component_type === "text") return <article key={component.component_id} className="rounded-lg border-l-4 border-teal-600 bg-slate-50/80 px-5 py-4 text-sm leading-7 text-slate-700">{component.content}</article>;
    if (component.component_type === "kpi") return <ReportMetric key={component.component_id} component={component} />;
    if (component.component_type === "table") return <DynamicTable key={component.component_id} component={component} />;
    return <ReportChart key={component.component_id} component={component} />;
  };
  return (
    <section className="space-y-10">
      <header className="border-b border-slate-200 pb-8">
        <div className="mb-3 flex items-center justify-between gap-3"><div className="flex min-w-0 items-center gap-2 text-[11px] font-bold uppercase tracking-[0.14em] text-teal-700"><FileBarChart className="size-4 shrink-0" />数据分析报告</div><StatusBadge status={report.status} /></div>
        <h2 className="max-w-5xl break-words text-2xl font-extrabold leading-tight tracking-tight text-slate-950">{report.title}</h2>
        <p className="mt-4 max-w-5xl border-l-2 border-slate-300 pl-4 text-base font-medium leading-7 text-slate-600">{report.summary}</p>
      </header>
      {report.sections.map((section, sectionIndex) => {
        const columns = Math.max(1, Math.min(section.layout?.columns || 1, 3));
        const isGrid = section.layout?.type === "grid" && columns > 1;
        const blocks = groupReportComponents(section.components);
        return <section key={section.title + "-" + sectionIndex} className="space-y-5">
         <div className="flex items-center gap-3"><span className="font-mono text-[11px] font-bold text-teal-700">{String(sectionIndex + 1).padStart(2, "0")}</span><h3 className="text-lg font-extrabold tracking-tight text-slate-900">{section.title}</h3><div className="h-px flex-1 bg-slate-200" /></div>
         <div className={isGrid ? "grid items-start gap-5" : "space-y-5"} style={isGrid ? { gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` } : undefined}>
           {blocks.map((block, blockIndex) => {
             if (block.kind === "kpi-row") {
               return <div key={`kpi-row-${blockIndex}`} className="grid min-w-0 items-stretch gap-5" style={{ gridTemplateColumns: `repeat(${block.components.length}, minmax(0, 1fr))`, ...(isGrid ? { gridColumn: "1 / -1" } : undefined) }}>
                 {block.components.map((component) => <div key={component.component_id} className="min-w-0">{renderComponent(component)}</div>)}
               </div>;
             }
             const component = block.component;
             const defaultSpan = component.component_type === "text" || component.component_type === "table" ? columns : 1;
             const span = Math.max(1, Math.min(component.span || defaultSpan, columns));
             return <div key={component.component_id} className="min-w-0" style={isGrid ? { gridColumn: `span ${span} / span ${span}` } : undefined}>{renderComponent(component)}</div>;
           })}
         </div>
       </section>;
      })}
      {report.limitations.length > 0 && <div className="border-t border-amber-200 pt-5"><div className="mb-2 flex items-center gap-2 text-xs font-extrabold text-amber-800"><AlertCircle className="size-4" />分析边界</div><ul className="space-y-1 text-xs leading-5 text-amber-900">{report.limitations.map((limitation, index) => <li key={limitation + "-" + index}>• {limitation}</li>)}</ul></div>}
    </section>
  );
}

function StepStatusIcon({ status }: { status: RunStep["status"] }) {
  if (status === "success") return <CheckCircle2 className="relative z-10 size-4 shrink-0 text-emerald-500" />;
  if (status === "failed") return <XCircle className="relative z-10 size-4 shrink-0 text-rose-500" />;
  if (status === "partial") return <AlertCircle className="relative z-10 size-4 shrink-0 text-amber-500" />;
  return <Loader2 className="relative z-10 size-4 shrink-0 animate-spin text-blue-500" />;
}

function TaskStatusIcon({ status }: { status: TaskStatus }) {
  if (status === "success") return <CheckCircle2 className="size-4 shrink-0 text-emerald-500" />;
  if (status === "failed") return <XCircle className="size-4 shrink-0 text-rose-500" />;
  if (status === "partial") return <AlertCircle className="size-4 shrink-0 text-amber-500" />;
  if (status === "pending") return <CircleHelp className="size-4 shrink-0 text-slate-400" />;
  return <Loader2 className="size-4 shrink-0 animate-spin text-blue-500" />;
}

function DebugEventDetails({ event, defaultOpen = false, title }: { event: DebugEvent; defaultOpen?: boolean; title?: string }) {
  const [open, setOpen] = useState(defaultOpen);
  const status = event.status || (event.type === "error" ? "failed" : "unknown");
  const statusConfig =
    status === "success"
      ? { label: "成功", className: "text-emerald-500", icon: CheckCircle2 }
      : status === "failed"
        ? { label: "失败", className: "text-rose-500", icon: XCircle }
        : status === "partial"
          ? { label: "部分完成", className: "text-amber-500", icon: AlertCircle }
          : status === "running"
            ? { label: "进行中", className: "animate-spin text-blue-500", icon: Loader2 }
            : { label: "未提供状态", className: "text-slate-400", icon: CircleHelp };
  const StatusIcon = statusConfig.icon;
  return (
    <details open={open} onToggle={(toggleEvent) => setOpen(toggleEvent.currentTarget.open)} className="group rounded-lg border border-slate-200 bg-white">
      <summary className="flex cursor-pointer list-none items-start gap-2 px-3 py-2.5">
        <ChevronRight className="mt-0.5 size-3.5 shrink-0 text-slate-400 transition-transform group-open:rotate-90" />
        <span className="mt-0.5 shrink-0" title={"状态：" + statusConfig.label} aria-label={"状态：" + statusConfig.label}>
          <StatusIcon className={"size-4 " + statusConfig.className} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block break-words text-[11px] font-bold text-slate-700">{title || event.step}</span>
          <span className="mt-0.5 block break-words text-[10px] text-slate-400">{debugEventLabel(event)}</span>
        </span>
        <span className="shrink-0 text-[9px] text-slate-400">{new Date(event.receivedAt).toLocaleTimeString("zh-CN")}</span>
      </summary>
      <div className="border-t border-slate-100 px-3 pb-3 pt-2">
        {event.truncatedFields?.length ? <div className="mb-2 rounded bg-amber-50 px-2 py-1.5 text-[10px] leading-4 text-amber-800">调试预览已截断：{event.truncatedFields.join("；")}</div> : null}
        <pre className="max-h-[360px] overflow-auto whitespace-pre-wrap break-words rounded bg-slate-950 p-3 text-[10px] leading-5 text-slate-100">{debugJson(event.payload)}</pre>
      </div>
    </details>
  );
}

function ReturnDrawer({
  title,
  subtitle,
  status,
  children,
  trigger,
}: {
  title: string;
  subtitle?: string;
  status?: TaskStatus;
  children: ReactNode;
  trigger: (open: () => void) => ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      {trigger(() => setOpen(true))}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent
          showCloseButton={false}
          style={{ width: "720px", maxWidth: "calc(100% - 24px)" }}
          className="fixed inset-y-0 right-0 left-auto top-0 z-50 grid h-full max-w-none translate-x-0 translate-y-0 gap-0 overflow-hidden rounded-none border-y-0 border-r-0 border-l border-slate-200 bg-slate-50 p-0 shadow-2xl duration-200 data-open:slide-in-from-right data-closed:slide-out-to-right"
        >
          <div className="min-h-0 overflow-y-auto">
            <div className="sticky top-0 z-20 border-b border-slate-200 bg-white px-4 py-3 shadow-[0_4px_12px_-10px_rgba(15,23,42,0.35)]">
              <div className="flex items-start gap-2">
                {status && <TaskStatusIcon status={status} />}
                <div className="min-w-0 flex-1">
                  <DialogTitle className="break-words text-sm font-extrabold text-slate-800">{title}</DialogTitle>
                  {subtitle && <div className="mt-1 break-words text-[10px] text-slate-400">{subtitle}</div>}
                </div>
                {status && <span className="shrink-0 text-[10px] font-bold text-slate-400">{status}</span>}
              </div>
            </div>
            <div className="space-y-3 p-4">{children}</div>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}

function ExecutionSteps({ steps }: { steps: RunStep[] }) {
  return (
    <div className="space-y-3">
      {steps.map((step, index) => (
        <div key={step.key} className="relative flex gap-3">
          {index < steps.length - 1 && <span className="absolute left-[7px] top-5 h-[calc(100%+12px)] w-px bg-slate-200" />}
          <StepStatusIcon status={step.status} />
          <div className="min-w-0 pb-1"><div className="text-xs font-bold text-slate-700">{step.label}</div>{step.detail && <div className="mt-1 break-words text-[11px] leading-5 text-slate-400">{step.detail}</div>}</div>
        </div>
      ))}
      {!steps.length && <div className="py-10 text-center text-xs text-slate-400">提交问题后显示执行步骤</div>}
    </div>
  );
}

function DebugEventsList({ events, emptyText }: { events: DebugEvent[]; emptyText: string }) {
  if (!events.length) return <div className="py-10 text-center text-xs text-slate-400">{emptyText}</div>;
  return <div className="space-y-2">{events.map((event) => <DebugEventDetails key={event.key} event={event} />)}</div>;
}

function StepReturnCard({ group }: { group: StepReturnGroup }) {
  const [open, setOpen] = useState(group.status === "failed");
  const displayGroups = buildDisplayEventGroups(group.events);
  useEffect(() => {
    if (group.status === "failed") setOpen(true);
  }, [group.status]);
  return (
    <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)} className="group rounded-lg border border-slate-200 bg-white">
      <summary className="flex cursor-pointer list-none items-start gap-2 px-3 py-3">
        <ChevronRight className="mt-0.5 size-3.5 shrink-0 text-slate-400 transition-transform group-open:rotate-90" />
        <span className="mt-0.5 shrink-0"><StepStatusIcon status={group.status} /></span>
        <span className="min-w-0 flex-1">
          <span className="block text-xs font-bold text-slate-700">{group.step}</span>
          <span className="mt-1 block text-[10px] text-slate-400">{group.taskGroups.length ? group.taskGroups.length + " 个分析任务" : group.nodeGroups.length + " 个 Agent 节点"} · 最新：{displayEventLabel(group.latest)}</span>
        </span>
        <span className="shrink-0 text-[10px] text-slate-400">{group.status}</span>
      </summary>
      <div className="space-y-3 border-t border-slate-100 px-3 pb-3 pt-2">
        {group.taskGroups.length
          ? group.taskGroups.map((taskGroup) => <StepTaskDetail key={taskGroup.taskId} group={taskGroup} />)
          : group.nodeGroups.length
            ? group.nodeGroups.map((nodeGroup) => <NodeReturnCard key={nodeGroup.node} group={nodeGroup} />)
            : displayGroups.map((item) => <DebugEventDetails key={item.key} event={item.event} title={item.label} />)}
      </div>
    </details>
  );
}

function StepTaskDetail({ group }: { group: TaskReturnGroup }) {
  const task = group.task;
  const phase = task?.node ? "当前节点：" + task.node : (task?.phase || (group.status === "pending" ? "等待执行" : "执行中"));
  return (
    <ReturnDrawer
      title={group.taskId}
      subtitle={group.steps.length + " 个任务步骤 · " + phase}
      status={group.status}
      trigger={(open) => (
        <button type="button" onClick={open} className="group w-full rounded-lg border border-slate-200 bg-slate-50/50 px-3 py-3 text-left transition-colors hover:border-blue-300 hover:bg-blue-50/60">
          <span className="flex items-start gap-2">
            <ChevronRight className="mt-0.5 size-3.5 shrink-0 text-slate-400" />
            <TaskStatusIcon status={group.status} />
            <span className="min-w-0 flex-1">
              <span className="block break-all text-xs font-bold text-slate-700">{group.taskId}</span>
              <span className="mt-1 block text-[10px] text-slate-500">{group.steps.length} 个任务步骤 · {phase}</span>
            </span>
            <span className="shrink-0 text-[10px] text-slate-400">查看详情</span>
          </span>
        </button>
      )}
    >
      <TaskDetailBody group={group} />
    </ReturnDrawer>
  );
}

function TaskDetailBody({ group }: { group: TaskReturnGroup }) {
  const task = group.task;
  return (
    <>
      <TaskMetadata task={task} />
      {group.steps.length
        ? <div className="space-y-2">{group.steps.map((step) => <StepTaskStepTree key={step.step} step={step} />)}</div>
        : <div className="py-3 text-center text-xs text-slate-400">该任务暂无详细返回</div>}
    </>
  );
}

function StepTaskStepTree({ step }: { step: TaskReturnStep }) {
  const [open, setOpen] = useState(step.status === "running" || step.status === "failed");
  useEffect(() => {
    setOpen(step.status === "running" || step.status === "failed");
  }, [step.status]);
  return (
    <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)} className="rounded-md border border-slate-200 bg-white">
      <summary className="flex cursor-pointer list-none items-start gap-2 px-3 py-2.5">
        <ChevronRight className="mt-0.5 size-3.5 shrink-0 text-slate-400 transition-transform group-open:rotate-90" />
        <StepStatusIcon status={step.status} />
        <span className="min-w-0 flex-1">
          <span className="block text-[11px] font-bold text-slate-700">{step.step}</span>
          <span className="mt-0.5 block text-[10px] text-slate-400">{step.nodeGroups.length} 个 Agent 节点 · 最新：{displayEventLabel(step.latest)}</span>
        </span>
        <span className="shrink-0 text-[10px] text-slate-400">{step.status}</span>
      </summary>
      <div className="space-y-2 border-t border-slate-100 px-3 pb-3 pt-2">
        {step.nodeGroups.length
          ? step.nodeGroups.map((nodeGroup) => <NodeReturnTree key={nodeGroup.node} group={nodeGroup} />)
          : step.displayGroups.map((item) => <DebugEventDetails key={item.key} event={item.event} title={item.label} />)}
      </div>
    </details>
  );
}

function NodeReturnTree({ group }: { group: NodeReturnGroup }) {
  const [open, setOpen] = useState(group.status === "running" || group.status === "failed");
  useEffect(() => {
    setOpen(group.status === "running" || group.status === "failed");
  }, [group.status]);
  return (
    <details open={open} onToggle={(event) => setOpen(event.currentTarget.open)} className="group rounded-md border border-blue-100 bg-blue-50/30">
      <summary className="flex cursor-pointer list-none items-start gap-2 px-3 py-2.5">
        <ChevronRight className="mt-0.5 size-3.5 shrink-0 text-slate-400 transition-transform group-open:rotate-90" />
        <StepStatusIcon status={group.status} />
        <span className="min-w-0 flex-1">
          <span className="block break-all text-[11px] font-bold text-slate-700">节点：{group.node}</span>
          <span className="mt-0.5 block text-[10px] text-slate-400">{group.displayGroups.length} 个返回项 · 最新：{displayEventLabel(group.latest)}</span>
        </span>
        <span className="shrink-0 text-[10px] text-slate-400">{group.status}</span>
      </summary>
      <div className="space-y-2 border-t border-blue-100 px-3 pb-3 pt-2">
        {group.displayGroups.map((item) => <DebugEventDetails key={item.key} event={item.event} title={item.label} />)}
      </div>
    </details>
  );
}

function NodeReturnCard({ group }: { group: NodeReturnGroup }) {
  return (
    <ReturnDrawer
      title={"节点：" + group.node}
      subtitle={group.displayGroups.length + " 个返回项 · 最新：" + displayEventLabel(group.latest)}
      status={group.status}
      trigger={(open) => (
        <button type="button" onClick={open} className="group w-full rounded-md border border-blue-100 bg-blue-50/30 px-3 py-2.5 text-left transition-colors hover:border-blue-300 hover:bg-blue-50">
          <span className="flex items-start gap-2">
            <ChevronRight className="mt-0.5 size-3.5 shrink-0 text-slate-400" />
            <StepStatusIcon status={group.status} />
            <span className="min-w-0 flex-1">
              <span className="block break-all text-[11px] font-bold text-slate-700">节点：{group.node}</span>
              <span className="mt-0.5 block text-[10px] text-slate-400">{group.displayGroups.length} 个返回项 · 最新：{displayEventLabel(group.latest)}</span>
            </span>
            <span className="shrink-0 text-[10px] text-slate-400">查看详情</span>
          </span>
        </button>
      )}
    >
      {group.displayGroups.map((item) => <DebugEventDetails key={item.key} event={item.event} title={item.label} />)}
    </ReturnDrawer>
  );
}

function TaskMetadata({ task }: { task?: TaskSummary }) {
  if (!task) return null;
  return (
    <div className="space-y-2 rounded-md border border-slate-200 bg-white px-3 py-2.5 text-[11px] leading-5">
      {task.question && <div><span className="font-bold text-slate-400">查询问题：</span><span className="text-slate-600">{task.question}</span></div>}
      {task.resolved_question && task.resolved_question !== task.question && <div><span className="font-bold text-slate-400">实际查询：</span><span className="text-slate-600">{task.resolved_question}</span></div>}
      {task.purpose && <div><span className="font-bold text-slate-400">任务用途：</span><span className="text-slate-600">{task.purpose}</span></div>}
      <div><span className="font-bold text-slate-400">依赖任务：</span><span className="text-slate-600">{task.depends_on.length ? task.depends_on.join("、") : "无"}</span></div>
      {task.node && <div><span className="font-bold text-slate-400">当前节点：</span><span className="font-mono text-slate-600">{task.node}</span></div>}
      {task.phase && <div><span className="font-bold text-slate-400">当前阶段：</span><span className="text-slate-600">{task.phase}</span></div>}
      {task.error && <div className="rounded bg-rose-50 px-2 py-1 text-rose-700">失败原因：{task.error}</div>}
    </div>
  );
}

function TaskDetailPanel({ group }: { group: TaskReturnGroup }) {
  return (
    <div className="min-w-0">
      <div className="sticky top-0 z-20 border-b border-slate-200 bg-white px-4 py-3 shadow-[0_4px_12px_-10px_rgba(15,23,42,0.35)]">
        <div className="flex items-start gap-2">
          <TaskStatusIcon status={group.status} />
          <div className="min-w-0 flex-1">
            <DialogTitle className="break-all text-xs font-extrabold text-slate-800">{group.taskId}</DialogTitle>
            <div className="mt-1 text-[10px] text-slate-400">{group.steps.length} 个任务步骤 · {group.events.length} 条任务事件</div>
          </div>
          <span className="shrink-0 text-[10px] font-bold text-slate-400">{group.status}</span>
        </div>
      </div>
      <div className="space-y-3 p-3">
        <TaskDetailBody group={group} />
      </div>
    </div>
  );
}

function TaskExplorer({ tasks }: { tasks: TaskSummary[] }) {
  // 桌面端用任务索引打开详情抽屉，避免把任务详情长期占据主列表空间。
  const taskGroups = buildTaskReturnGroups(tasks);
  const [selectedTaskId, setSelectedTaskId] = useState(tasks[0]?.task_id || "");
  const [drawerOpen, setDrawerOpen] = useState(false);

  useEffect(() => {
    if (!taskGroups.some((group) => group.taskId === selectedTaskId)) {
      setSelectedTaskId(taskGroups[0]?.taskId || "");
    }
  }, [selectedTaskId, taskGroups]);

  const selectedGroup = taskGroups.find((group) => group.taskId === selectedTaskId);
  if (!taskGroups.length) return <div className="py-10 text-center text-xs text-slate-400">复杂分析任务返回后，这里会按 task_id 展示</div>;

  return (
    <>
      <div className="min-h-[420px] overflow-hidden rounded-lg border border-slate-200 bg-white">
        <div className="flex items-center justify-between border-b border-slate-200 px-3 py-3">
          <span className="text-[11px] font-extrabold text-slate-700">任务列表</span>
          <span className="font-mono text-[10px] text-slate-400">{taskGroups.length}</span>
        </div>
        <div className="space-y-1 p-2">
          {taskGroups.map((group) => {
            const task = group.task;
            const phase = task?.phase || (group.status === "pending" ? "等待执行" : group.status === "running" ? "执行中" : "已完成");
            return (
              <button
                key={group.taskId}
                type="button"
                onClick={() => {
                  setSelectedTaskId(group.taskId);
                  setDrawerOpen(true);
                }}
                className="w-full rounded-md border border-slate-200 bg-white px-3 py-3 text-left shadow-sm transition-colors hover:border-blue-300 hover:bg-blue-50/50"
              >
                <span className="flex items-start gap-2">
                  <TaskStatusIcon status={group.status} />
                  <span className="min-w-0 flex-1">
                    <span className="block break-all text-[11px] font-extrabold text-slate-700">{group.taskId}</span>
                    <span className="mt-1 block truncate text-[10px] text-slate-400">{phase} · {group.steps.length} 个步骤</span>
                  </span>
                  <ChevronRight className="mt-0.5 size-4 shrink-0 text-slate-300" />
                </span>
              </button>
            );
          })}
        </div>
      </div>
      <Dialog open={drawerOpen} onOpenChange={setDrawerOpen}>
        {selectedGroup && (
          <DialogContent
            showCloseButton={false}
            style={{ width: "720px", maxWidth: "calc(100% - 24px)" }}
            className="fixed inset-y-0 right-0 left-auto top-0 z-50 grid h-full max-w-none translate-x-0 translate-y-0 gap-0 overflow-hidden rounded-none border-y-0 border-r-0 border-l border-slate-200 bg-slate-50 p-0 shadow-2xl duration-200 data-open:slide-in-from-right data-closed:slide-out-to-right"
          >
            <div className="min-h-0 overflow-y-auto">
              <TaskDetailPanel key={selectedGroup.taskId} group={selectedGroup} />
            </div>
          </DialogContent>
        )}
      </Dialog>
    </>
  );
}

function ExecutionPanel({ running, debugEvents }: { running: boolean; debugEvents: DebugEvent[] }) {
  // 四个 Tab 分别承载总进度、主步骤返回、分析任务和原始事件。
  const allEvents = orderedDebugEvents(debugEvents);
  const taskSummaries = buildTaskSummaries(allEvents);
  const progressSteps = buildProgressSteps(allEvents, taskSummaries);
  const stepReturnGroups = buildStepReturnGroups(allEvents, taskSummaries);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [activeTab, setActiveTab] = useState("steps");

  const handleTabChange = (value: string | null) => {
    if (!value) return;
    setActiveTab(value);
    scrollRef.current?.scrollTo({ top: 0, behavior: "auto" });
    requestAnimationFrame(() => scrollRef.current?.scrollTo({ top: 0, behavior: "auto" }));
  };

  const completedTaskCount = taskSummaries.filter((task) => isTerminalStatus(task.status)).length;
  const progressPercent = taskSummaries.length ? (completedTaskCount / taskSummaries.length) * 100 : 0;

  return (
    <aside className="flex min-h-0 w-full shrink-0 flex-col border-l border-slate-200 bg-white xl:w-[430px] 2xl:w-[500px]">
      <div className="border-b border-slate-200 px-5 py-4">
        <div className="flex items-center gap-2 text-sm font-extrabold text-slate-800"><ClipboardList className="size-4 text-blue-600" />执行过程</div>
        <p className="mt-1 text-[11px] text-slate-400">实时展示本次分析的任务进度与节点返回</p>
      </div>
      <Tabs value={activeTab} onValueChange={handleTabChange} className="min-h-0 flex-1 gap-0">
        <TabsList variant="line" className="relative z-20 grid h-12 w-full shrink-0 grid-cols-4 justify-stretch overflow-x-auto rounded-none border-b border-slate-200 bg-white px-5 py-0 shadow-[0_4px_10px_-8px_rgba(15,23,42,0.35)]">
            <TabsTrigger value="steps" className="gap-1.5 text-[11px]"><ClipboardList className="size-3.5" />进度</TabsTrigger>
            <TabsTrigger value="returns" className="gap-1.5 text-[11px]"><Braces className="size-3.5" />步骤返回{stepReturnGroups.length ? " " + stepReturnGroups.length : ""}</TabsTrigger>
            <TabsTrigger value="tasks" className="gap-1.5 text-[11px]"><Layers3 className="size-3.5" />分析任务{taskSummaries.length ? " " + taskSummaries.length : ""}</TabsTrigger>
            <TabsTrigger value="all" className="gap-1.5 text-[11px]"><Braces className="size-3.5" />全部事件{allEvents.length ? " " + allEvents.length : ""}</TabsTrigger>
        </TabsList>
        <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
          <TabsContent value="steps" className="mt-4 space-y-4 px-5 pb-5">
            <ExecutionSteps steps={progressSteps} />
            {taskSummaries.length > 0 && <div className="border-t border-slate-100 pt-4"><div className="mb-2 flex items-center justify-between text-[11px] font-bold text-slate-400"><span>分析任务总进度</span><span>{completedTaskCount} / {taskSummaries.length}</span></div><div className="h-1.5 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full bg-blue-500 transition-all" style={{ width: progressPercent + "%" }} /></div></div>}
          </TabsContent>
          <TabsContent value="returns" className="mt-4 space-y-2 px-5 pb-5">
            {!stepReturnGroups.length && <div className="py-10 text-center text-xs text-slate-400">收到节点返回后，这里会显示主步骤摘要</div>}
            {stepReturnGroups.map((group) => <StepReturnCard key={group.step} group={group} />)}
          </TabsContent>
          <TabsContent value="tasks" className="mt-4 space-y-3 px-5 pb-5">
            <TaskExplorer tasks={taskSummaries} />
          </TabsContent>
          <TabsContent value="all" className="mt-4 px-5 pb-5"><DebugEventsList events={allEvents} emptyText="收到 SSE 事件后，这里会显示全部 JSON" /></TabsContent>
        </div>
      </Tabs>
      <div className="border-t border-slate-200 px-5 py-3"><div className="flex items-center gap-2 text-[11px] font-semibold text-slate-400"><Timer className="size-3.5" />{running ? "正在执行" : debugEvents.length ? "本次执行已结束" : "等待提问"}</div></div>
    </aside>
  );
}

export default function AnalysisWorkspace() {
  // 当前会话工作台：提交问题、接收执行事件并渲染唯一的最终报告。
  const [question, setQuestion] = useState("");
  const [sessionId, setSessionId] = useState(FIXED_REPORT_MODE ? "fixed-report-debug" : "");
  const [sessionStatus, setSessionStatus] = useState(FIXED_REPORT_MODE ? "固定报告渲染调试" : "初始化会话");
  const [running, setRunning] = useState(false);
  const [report, setReport] = useState<RenderedReport | null>(FIXED_REPORT_MODE ? FIXED_RENDERED_REPORT : null);
  const [debugEvents, setDebugEvents] = useState<DebugEvent[]>([]);
  const [error, setError] = useState("");
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (FIXED_REPORT_MODE) return;
    // 初始化当前浏览器会话，并通过 GET 读取工作台状态。
    const storageKey = "insight-agent:session-id";
    const currentSessionId = sessionStorage.getItem(storageKey) || crypto.randomUUID();
    sessionStorage.setItem(storageKey, currentSessionId);
    setSessionId(currentSessionId);
    void fetch(`/api/analysis?session_id=${encodeURIComponent(currentSessionId)}`, { cache: "no-store", headers: { Accept: "application/json" } })
      .then(async (response) => {
        if (!response.ok) throw new Error("会话状态读取失败");
        const data = (await response.json()) as { status?: string };
        setSessionStatus(data.status === "ready" ? "会话已就绪" : "会话待连接");
      })
      .catch(() => setSessionStatus("会话待连接"));
  }, []);

  const resetOutput = useCallback(() => {
    if (FIXED_REPORT_MODE) {
      setReport(FIXED_RENDERED_REPORT);
      setDebugEvents([]);
      setError("");
      return;
    }
    // 开始新一轮分析前清理当前会话的运行态和最终报告。
    setReport(null);
    setDebugEvents([]);
    setError("");
  }, []);

  const runQuestion = useCallback(async () => {
    if (FIXED_REPORT_MODE) return;
    // 提交问题并消费主流程事件，最终只接受 rendered_report 作为正式产物。
    const normalizedQuestion = question.trim();
    if (!normalizedQuestion || running || !sessionId) return;
    resetOutput();
    setRunning(true);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      for await (const event of streamAgent(normalizedQuestion, sessionId, controller.signal)) {
        setDebugEvents((current) => upsertDebugEvent(current, event));
        if (event.type === "rendered_report" && event.rendered_report) setReport(event.rendered_report);
        if (event.type === "error") setError(String(event.message || event.error || "分析失败"));
      }
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "分析请求失败");
    } finally {
      setRunning(false);
      abortRef.current = null;
    }
  }, [question, resetOutput, running, sessionId]);

  const cancelQuestion = () => {
    // 中止当前 SSE 请求，保留已收到的执行信息。
    abortRef.current?.abort();
    setRunning(false);
  };

  const status = report?.status || (running ? "running" : error ? "failed" : debugEvents.length ? "partial" : "");
  const hasOutput = Boolean(report || error);

  return (
    <div className="flex h-full min-h-0 w-full min-w-0 flex-1 flex-col xl:flex-row">
      <section className="flex min-w-0 flex-1 flex-col">
        <div className="border-b border-slate-200 bg-white px-5 py-4 lg:px-8"><div className="mx-auto flex max-w-[1400px] items-start justify-between gap-4"><div className="min-w-0"><div className="flex items-center gap-2"><MessageSquare className="size-4 text-blue-600" /><h1 className="truncate text-base font-extrabold text-slate-900">{report?.title || "当前分析会话"}</h1><StatusBadge status={status} /></div><p className="mt-1 truncate text-xs text-slate-400">{sessionStatus} · 会话 ID：{sessionId || "初始化中"}</p></div>{hasOutput && <Button type="button" variant="ghost" size="icon" onClick={() => { setQuestion(""); resetOutput(); }} title="新建当前会话"><RotateCcw className="size-4" /></Button>}</div></div>
        <div className="min-h-0 flex-1 overflow-y-auto"><div className="mx-auto max-w-[1400px] space-y-6 px-5 py-6 lg:px-8">
          {!hasOutput && !running && <div className="flex min-h-[min(46vh,520px)] flex-col items-center justify-center text-center"><div className="mb-5 flex size-16 items-center justify-center rounded-2xl bg-blue-50 text-blue-600"><FileBarChart className="size-8" /></div><h2 className="text-2xl font-extrabold tracking-tight text-slate-900">用自然语言开始数据分析</h2><p className="mt-2 max-w-md text-sm leading-6 text-slate-500">输入一个问数或分析问题，系统会在当前会话中展示执行过程与最终报告。</p><Button type="button" variant="outline" className="mt-5 gap-2" onClick={() => setQuestion(DEFAULT_QUESTION)}><Play className="size-3.5" />填入示例问题</Button></div>}
          {running && !report && <div className="border-l-2 border-blue-500 px-4 py-3 text-sm font-semibold text-slate-600">正在生成报告：{question}</div>}
          {report && <ReportView report={report} />}
          {error && <div className="flex items-start gap-3 border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700"><AlertCircle className="mt-0.5 size-4 shrink-0" /><span>{error}</span></div>}
        </div></div>
          <div className="border-t border-slate-200 bg-white px-5 py-4 lg:px-8"><div className="mx-auto flex max-w-[1400px] gap-3"><Textarea value={question} readOnly={FIXED_REPORT_MODE} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); void runQuestion(); } }} placeholder={FIXED_REPORT_MODE ? "当前为固定报告渲染调试模式" : "例如：找出2017年销售额下降最明显的月份，并分析该月份下降最多的商品类别和卖家地区。"} className="min-h-12 max-h-32 resize-none bg-slate-50 text-sm focus-visible:bg-white" /><Button type="button" disabled={FIXED_REPORT_MODE || !question.trim() || running || !sessionId} onClick={() => void runQuestion()} className="h-12 w-12 shrink-0 p-0" title="发送问题">{running ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}</Button>{running && <Button type="button" variant="outline" onClick={cancelQuestion} className="h-12 w-12 shrink-0 p-0" title="停止分析"><XCircle className="size-4" /></Button>}</div></div>
      </section>
      <ExecutionPanel running={running} debugEvents={debugEvents} />
    </div>
  );
}
