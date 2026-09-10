"use client";

import { useEffect, useMemo, useRef, useState } from "react";
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
import { AlertCircle, BarChart3, ChevronRight, FileBarChart, Table2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";

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

export interface RenderedReport {
  status: "success" | "partial" | "failed";
  title: string;
  summary: string;
  sections: ReportSection[];
  limitations: string[];
}

function textValue(value: JsonValue | undefined) {
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
  if (unit === "currency") return formatted + " 元";
  if (unit === "percent") {
    return (value * 100).toLocaleString("zh-CN", { maximumFractionDigits: 2 }) + "%";
  }
  return formatted;
}

function formatAxisValue(value: number, unit?: string | null) {
  if (unit === "percent") {
    return (value * 100).toLocaleString("zh-CN", { maximumFractionDigits: 1 }) + "%";
  }
  const absolute = Math.abs(value);
  if (unit === "currency" && absolute >= 100000000) {
    return (value / 100000000).toLocaleString("zh-CN", { maximumFractionDigits: 1 }) + " 亿";
  }
  if (unit === "currency" && absolute >= 10000) {
    return (value / 10000).toLocaleString("zh-CN", { maximumFractionDigits: 1 }) + " 万";
  }
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 1 });
}

function formatCell(value: JsonValue | undefined, column?: ReportColumn) {
  const number = numericValue(value);
  const isNumericColumn =
    typeof value === "number" ||
    column?.field_role === "metric" ||
    column?.field_role === "derived_metric" ||
    Boolean(column?.unit);
  if (number !== null && isNumericColumn) return formatNumber(number, column?.unit);
  return textValue(value);
}

function inferMetricUnit(title: string, valueField = "") {
  const hint = title + " " + valueField;
  if (hint.includes("变化率") || hint.includes("降幅") || hint.includes("率") || valueField === "change_rate") {
    return "percent";
  }
  if (hint.includes("金额") || hint.includes("销售额") || hint.includes("价格") || valueField === "decrease_amount") {
    return "currency";
  }
  return undefined;
}

function metricValue(value: JsonValue | undefined, title: string, valueField?: string) {
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
    if (key in objectValue && objectValue[key] !== null && objectValue[key] !== undefined) {
      return objectValue[key];
    }
  }
  const firstScalar = Object.values(objectValue).find((item) => numericValue(item) !== null);
  return firstScalar ?? null;
}

function displayName(column: ReportColumn | undefined, fallback: string) {
  return column?.display_name || fallback;
}

function StatusBadge({ status }: { status: string }) {
  const config =
    status === "success"
      ? { label: "分析完成", className: "border-emerald-200 bg-emerald-50 text-emerald-700" }
      : status === "partial"
        ? { label: "部分完成", className: "border-amber-200 bg-amber-50 text-amber-700" }
        : status === "failed"
          ? { label: "分析失败", className: "border-rose-200 bg-rose-50 text-rose-700" }
          : { label: "处理中", className: "border-slate-200 bg-slate-50 text-slate-500" };
  return <Badge variant="outline" className={config.className}>{config.label}</Badge>;
}

function ReportBindingError({ component }: { component: ReportComponent }) {
  return (
    <section className="border border-rose-200 bg-rose-50/70 px-4 py-3">
      <div className="flex items-center gap-2 text-sm font-bold text-rose-700">
        <AlertCircle className="size-4" />
        {component.title}
      </div>
      <p className="mt-2 text-xs leading-5 text-rose-700">
        {component.binding_error || "组件数据绑定失败。"}
      </p>
    </section>
  );
}

function ReportMetric({ component }: { component: ReportComponent }) {
  if (component.binding_status === "failed") return <ReportBindingError component={component} />;
  const value = metricValue(component.value, component.title, component.value_field);
  const number = numericValue(value);
  const unit = inferMetricUnit(component.title, component.value_field);
  return (
    <section className="h-full min-w-0 rounded-lg border border-slate-200/90 bg-white px-5 py-4 shadow-[0_8px_24px_-20px_rgba(15,23,42,0.65)]">
      <h4 className="break-words text-sm font-bold leading-5 text-slate-700">{component.title}</h4>
      <div className={"mt-3 break-words text-2xl font-extrabold tracking-tight " + (number !== null && number < 0 ? "text-rose-700" : "text-slate-950")}>
        {number !== null ? formatNumber(number, unit) : textValue(value)}
      </div>
    </section>
  );
}

function DynamicTable({ component }: { component: ReportComponent }) {
  const [expanded, setExpanded] = useState(false);
  if (component.binding_status === "failed") return <ReportBindingError component={component} />;
  const rows = component.data || [];
  const previewRows = expanded ? rows : rows.slice(0, 10);
  return (
    <section className="overflow-hidden rounded-lg border border-slate-200/90 bg-white shadow-[0_8px_24px_-20px_rgba(15,23,42,0.65)]">
      <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <div className="flex min-w-0 items-start gap-2.5">
          <Table2 className="mt-0.5 size-4 shrink-0 text-teal-700" />
          <div className="min-w-0">
            <h3 className="break-words text-sm font-extrabold leading-5 text-slate-800">{component.title}</h3>
            <p className="mt-1 text-[11px] text-slate-400">明细数据</p>
          </div>
        </div>
        <span className="shrink-0 text-[11px] font-semibold text-slate-400">共 {component.row_count} 行</span>
      </div>
      <div className="max-h-[380px] overflow-auto">
        <table className="min-w-full text-left text-xs">
          <thead className="sticky top-0 z-10 bg-slate-50 text-[11px] font-bold text-slate-500 shadow-[0_1px_0_#e2e8f0]">
            <tr>
              {component.columns.map((column) => (
                <th key={column.result_name} className="whitespace-nowrap px-5 py-3">
                  {displayName(column, column.result_name)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {previewRows.map((row, rowIndex) => (
              <tr key={rowIndex} className="hover:bg-slate-50/80">
                {component.columns.map((column) => (
                  <td key={column.result_name} className="whitespace-nowrap px-5 py-2.5 text-slate-700">
                    {formatCell(row[column.result_name], column)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(rows.length > 10 || component.truncated) && (
        <div className="flex items-center justify-between gap-3 border-t border-slate-100 px-5 py-3 text-[11px] text-slate-400">
          <span>
            {expanded ? "当前展示 " + rows.length + " 行" : "报告先展示前 " + Math.min(rows.length, 10) + " 行"}
            {component.truncated ? "，完整结果由后端保留" : ""}
          </span>
          {rows.length > 10 && (
            <button type="button" onClick={() => setExpanded((current) => !current)} className="inline-flex shrink-0 items-center gap-1 font-bold text-teal-700 hover:text-teal-900">
              {expanded ? "收起明细" : "展开全部 " + rows.length + " 行"}
              <ChevronRight className={"size-3.5 transition-transform " + (expanded ? "rotate-90" : "")} />
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function chartCategoryLabel(value: unknown) {
  const label = String(value ?? "");
  return label.length > 14 ? label.slice(0, 14) + "…" : label;
}

function escapeTooltipText(value: unknown) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function ReportChart({ component }: { component: ReportComponent }) {
  if (component.binding_status === "failed") return <ReportBindingError component={component} />;
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

function EChartsReportChart({
  component,
  dimension,
  metricColumns,
}: {
  component: ReportComponent;
  dimension: ReportColumn;
  metricColumns: ReportColumn[];
}) {
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
    ? "展示 " + preparedRows.length + " / " + component.row_count + " 个数据点"
    : component.row_count + " 个数据点";
  const option = useMemo<EChartsOption>(() => {
    const colors = presentation.color_scheme === "teal"
      ? ["#0f766e", "#14b8a6", "#5eead4"]
      : presentation.color_scheme === "amber"
        ? ["#d97706", "#f59e0b", "#fbbf24"]
        : ["#2563eb", "#60a5fa", "#93c5fd"];
    const categories = preparedRows.map((row) => textValue(row[dimension.result_name]));
    const series: SeriesOption[] = metricColumns.map((column) => ({
      type: isLine ? "line" : "bar",
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
      const lines = items.map((item) => {
        const number = numericValue(item.value as JsonValue);
        const seriesColumn = metricColumns[item.seriesIndex ?? 0] || metric;
        return "<div>" + escapeTooltipText(item.seriesName || "指标") + "：" + (number === null ? "--" : formatNumber(number, seriesColumn.unit)) + "</div>";
      }).join("");
      return "<div><strong>" + escapeTooltipText(category) + "</strong>" + lines + "</div>";
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
            { type: "inside", xAxisIndex: 0, filterMode: "none", start: 0, end: 100 },
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
      legend: { show: Boolean(presentation.show_legend), top: 4, type: "scroll" },
      xAxis: isHorizontal ? valueAxis : categoryAxis,
      yAxis: isHorizontal ? { ...categoryAxis, inverse: true, axisLabel: { formatter: chartCategoryLabel } } : valueAxis,
      series,
    };
  }, [dimension.result_name, isHorizontal, isLine, metric.unit, metricColumns, preparedRows, presentation.color_scheme, presentation.show_labels, presentation.show_legend]);
  useEffect(() => {
    if (!chartRef.current) return;
    const chart = echarts.init(chartRef.current, undefined, { renderer: "canvas" });
    chartInstanceRef.current = chart;
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => chart.resize());
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
  const chartHeight = isHorizontal ? Math.max(300, Math.min(620, preparedRows.length * 32 + 64)) : isLine ? 360 : 320;
  return (
    <section className="overflow-hidden rounded-lg border border-slate-200/90 bg-white shadow-[0_8px_24px_-20px_rgba(15,23,42,0.65)]">
      <div className="flex items-start justify-between gap-3 border-b border-slate-100 px-5 py-4">
        <div className="flex min-w-0 items-start gap-2.5">
          <BarChart3 className="mt-0.5 size-4 shrink-0 text-blue-700" />
          <div className="min-w-0">
            <h3 className="break-words text-sm font-extrabold leading-5 text-slate-800">{component.title}</h3>
            <p className="mt-1 text-[11px] text-slate-400">{metricColumns.map((column) => displayName(column, column.result_name)).join("、")} · {chartRowsLabel}</p>
          </div>
        </div>
        <span className="shrink-0 text-[10px] font-semibold uppercase tracking-[0.08em] text-slate-400">{isLine ? "趋势" : "对比"}</span>
      </div>
      <div className="min-w-0 px-3 pb-3 pt-2" style={{ height: chartHeight }}>
        <div ref={chartRef} className="h-full min-w-0 w-full" role="img" aria-label={component.title} />
      </div>
    </section>
  );
}

type ReportBlock =
  | { kind: "component"; component: ReportComponent }
  | { kind: "kpi-row"; components: ReportComponent[] };

function groupReportComponents(components: ReportComponent[]): ReportBlock[] {
  const blocks: ReportBlock[] = [];
  let kpiGroup: ReportComponent[] = [];
  const flushKpiGroup = () => {
    if (kpiGroup.length >= 2) blocks.push({ kind: "kpi-row", components: kpiGroup });
    else if (kpiGroup.length === 1) blocks.push({ kind: "component", component: kpiGroup[0] });
    kpiGroup = [];
  };
  for (const component of components) {
    if (component.component_type === "kpi") kpiGroup.push(component);
    else {
      flushKpiGroup();
      blocks.push({ kind: "component", component });
    }
  }
  flushKpiGroup();
  return blocks;
}

export function ReportView({ report }: { report: RenderedReport }) {
  const renderComponent = (component: ReportComponent) => {
    if (component.binding_status === "failed") return <ReportBindingError key={component.component_id} component={component} />;
    if (component.component_type === "text") {
      return <article key={component.component_id} className="rounded-lg border-l-4 border-teal-600 bg-slate-50/80 px-5 py-4 text-sm leading-7 text-slate-700">{component.content}</article>;
    }
    if (component.component_type === "kpi") return <ReportMetric key={component.component_id} component={component} />;
    if (component.component_type === "table") return <DynamicTable key={component.component_id} component={component} />;
    return <ReportChart key={component.component_id} component={component} />;
  };
  return (
    <section className="space-y-10">
      <header className="border-b border-slate-200 pb-8">
        <div className="mb-3 flex items-center justify-between gap-3">
          <div className="flex min-w-0 items-center gap-2 text-[11px] font-bold uppercase tracking-[0.14em] text-teal-700">
            <FileBarChart className="size-4 shrink-0" />
            数据分析报告
          </div>
          <StatusBadge status={report.status} />
        </div>
        <h2 className="max-w-5xl break-words text-2xl font-extrabold leading-tight tracking-tight text-slate-950">{report.title}</h2>
        <p className="mt-4 max-w-5xl border-l-2 border-slate-300 pl-4 text-base font-medium leading-7 text-slate-600">{report.summary}</p>
      </header>
      {report.sections.map((section, sectionIndex) => {
        const columns = Math.max(1, Math.min(section.layout.columns || 1, 3));
        const isGrid = section.layout.type === "grid" && columns > 1;
        const blocks = groupReportComponents(section.components);
        return (
          <section key={section.title + "-" + sectionIndex} className="space-y-5">
            <div className="flex items-center gap-3">
              <span className="font-mono text-[11px] font-bold text-teal-700">{String(sectionIndex + 1).padStart(2, "0")}</span>
              <h3 className="text-lg font-extrabold tracking-tight text-slate-900">{section.title}</h3>
              <div className="h-px flex-1 bg-slate-200" />
            </div>
            <div className={isGrid ? "grid items-start gap-5" : "space-y-5"} style={isGrid ? { gridTemplateColumns: "repeat(" + columns + ", minmax(0, 1fr))" } : undefined}>
              {blocks.map((block, blockIndex) => {
                if (block.kind === "kpi-row") {
                  return (
                    <div key={"kpi-row-" + blockIndex} className="grid min-w-0 items-stretch gap-5" style={{ gridTemplateColumns: "repeat(" + block.components.length + ", minmax(0, 1fr))", ...(isGrid ? { gridColumn: "1 / -1" } : undefined) }}>
                      {block.components.map((component) => <div key={component.component_id} className="min-w-0">{renderComponent(component)}</div>)}
                    </div>
                  );
                }
                const component = block.component;
                const defaultSpan = component.component_type === "text" || component.component_type === "table" ? columns : 1;
                const span = Math.max(1, Math.min(component.span || defaultSpan, columns));
                return <div key={component.component_id} className="min-w-0" style={isGrid ? { gridColumn: "span " + span + " / span " + span } : undefined}>{renderComponent(component)}</div>;
              })}
            </div>
          </section>
        );
      })}
      {report.limitations.length > 0 && (
        <div className="border-t border-amber-200 pt-5">
          <div className="mb-2 flex items-center gap-2 text-xs font-extrabold text-amber-800"><AlertCircle className="size-4" />分析边界</div>
          <ul className="space-y-1 text-xs leading-5 text-amber-900">
            {report.limitations.map((limitation, index) => <li key={limitation + "-" + index}>• {limitation}</li>)}
          </ul>
        </div>
      )}
    </section>
  );
}
