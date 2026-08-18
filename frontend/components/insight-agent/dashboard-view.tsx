"use client";

import React, { useState, useEffect } from 'react';
import { 
  ArrowUp, 
  ArrowDown, 
  Check, 
  Clock, 
  Edit3, 
  Table as TableIcon, 
  ShieldCheck, 
  Database, 
  X, 
  Layers, 
  Copy,
  Info,
} from 'lucide-react';
import { mockDashboardInfo, generatedSQLContent } from '../../data/insight-agent';
import { StepDetail } from '../../types/insight-agent';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

export default function DashboardView() {
  /** 静态分析看板演示页：展示步骤、SQL、来源和审计 Tab 的目标样式。 */

  const [activeTab, setActiveTab] = useState<'steps' | 'sql' | 'sources' | 'audit'>('steps');
  const [copied, setCopied] = useState(false);
  const [selectedTable, setSelectedTable] = useState('orders');
  
  // Interactive execution steps
  const steps: StepDetail[] = [
    { id: '1', name: '问题理解', status: 'completed', duration: '00:04', description: '解析自然语言，提取主体与过滤条件 (本月, 商品, 销售趋势)' },
    { id: '2', name: '语义检索', status: 'completed', duration: '00:06', description: '匹配数据字典与业务术语，发现销售额 (sales_amount) 与下单时间 (order_purchase_timestamp)' },
    { id: '3', name: '指标解析', status: 'completed', duration: '00:05', description: '引用已发布统计口径 SUM(order_items.price)，匹配可用维度' },
    { id: '4', name: 'SQL 生成', status: 'completed', duration: '00:12', description: '智能生成 PostgreSQL 兼容的多表 JOIN 复杂汇总查询语句' },
    { id: '5', name: 'SQL 审核', status: 'completed', duration: '00:06', description: '安全沙箱阻断写操作、校验行数限制(LIMIT 1000)与越权审计' },
    { id: '6', name: '数据分析', status: 'running', duration: '00:18', description: '连接 Olist PostgreSQL 数据库，异步计算聚合多维指标中' },
    { id: '7', name: '报告生成', status: 'pending', duration: '--:--', description: '根据数据特性推荐图表方案，组装文字解读摘要报告' },
  ];

  // SQL State details lists
  const sqlAdvices = [
    '已为表添加合适的 JOIN 关联条件，避免产生笛卡尔积。',
    '使用的是 DATE() 函数进行日期分组，建议对关联字段创建 order_purchase_timestamp 索引。',
    '已限制结果集大小为 1000 行 (LIMIT 1000)，确保后端吞吐平稳。',
  ];

  const safetyChecks = [
    { label: 'SELECT only 只读校验', passed: true },
    { label: '无 DDL / DML 写入操作', passed: true },
    { label: '无越权或敏感字段访问', passed: true },
    { label: '查询行数限制已强制启用 (LIMIT 1000)', passed: true },
  ];

  const executionPlans = [
    { stage: 1, action: 'Seq Scan on orders', rows: '1.2M', cost: '320ms' },
    { stage: 2, action: 'Index Scan on order_items', rows: '3.5M', cost: '480ms' },
    { stage: 3, action: 'Index Scan on payments', rows: '1.2M', cost: '210ms' },
    { stage: 4, action: 'Index Scan on customers', rows: '100K', cost: '50ms' },
    { stage: 5, action: 'Hash Aggregate 内存聚合', rows: '1K', cost: '80ms' },
  ];

  // Database Tables in "Sources" Tab
  const databaseTables = [
    { name: 'orders', nick: '订单表', status: '已命中语义层', desc: '存储订单的基本状态，包含订单单号、客户ID、下单时间等', cols: 8 },
    { name: 'order_items', nick: '订单明细表', status: '已命中语义层', desc: '存储订单中每个商品的明细信息，包括商品ID、价格、数量等', cols: 7 },
    { name: 'products', nick: '商品表', status: '已命中语义层', desc: '存储商品的基本信息，包括商品名称、品类、价格等', cols: 6 },
    { name: 'customers', nick: '客户表', status: '已命中语义层', desc: '存储客户的基本信息，包括客户位置、注册时间等', cols: 5 },
    { name: 'payments', nick: '支付表', status: '已命中语义层', desc: '存储支付信息，包括支付方式、支付金额、时间等', cols: 4 },
  ];

  const fieldList = [
    { field: 'order_id', table: 'orders', type: 'varchar', status: '已命中语义层' },
    { field: 'customer_id', table: 'orders', type: 'varchar', status: '已命中语义层' },
    { field: 'order_status', table: 'orders', type: 'varchar', status: '已命中语义层' },
    { field: 'order_purchase_timestamp', table: 'orders', type: 'timestamp', status: '已命中语义层' },
    { field: 'order_id', table: 'order_items', type: 'varchar', status: '已命中语义层' },
    { field: 'product_id', table: 'order_items', type: 'varchar', status: '已命中语义层' },
    { field: 'price', table: 'order_items', type: 'numeric', status: '已命中语义层' },
    { field: 'freight_value', table: 'order_items', type: 'numeric', status: '已命中语义层' },
    { field: 'product_id', table: 'products', type: 'varchar', status: '已命中语义层' },
    { field: 'product_category_name', table: 'products', type: 'varchar', status: '已命中语义层' },
  ];

  // Audit list
  const auditLogs = [
    { time: '2024-05-19 10:30:21', result: '通过', type: '系统自动审核', manVer: '否' },
    { time: '2024-05-19 10:30:20', result: '通过', type: '系统自动审核', manVer: '否' },
    { time: '2024-05-19 10:30:18', result: '通过', type: '规则引擎校验', manVer: '否' },
    { time: '2024-05-19 10:30:16', result: '通过', type: '权限校验', manVer: '否' },
    { time: '2024-05-19 10:30:15', result: '通过', type: '安全扫描', manVer: '否' },
  ];

  const handleCopy = () => {
    /** 复制演示 SQL 到剪贴板，并短暂显示复制成功状态。 */

    navigator.clipboard.writeText(generatedSQLContent);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex-1 flex overflow-hidden bg-slate-50 relative font-sans">
      
      {/* 1. Main Left-Center Canvas Dashboard Section (Width adapts) */}
      <div className="flex-1 overflow-y-auto px-6 py-6 border-r border-[#e2e8f0]">
        
        {/* Title and Metadata Header */}
        <div className="bg-white p-5 rounded-2xl border border-slate-200/80 shadow-xs mb-5">
          <div className="flex items-center gap-2 mb-3.5">
            <h2 className="text-xl font-extrabold text-slate-800 tracking-tight">本月商品销售趋势分析</h2>
            <Button variant="ghost" className="h-7 w-7 rounded p-0 text-slate-400 hover:bg-slate-100 hover:text-slate-600">
              <Edit3 className="w-4 h-4" />
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-y-2 gap-x-6 text-[11px] font-semibold text-slate-500">
            <div>时间范围：<span className="text-slate-800 font-bold">{mockDashboardInfo.timeRange}</span></div>
            <div>创建时间：<span className="text-slate-800 font-bold">{mockDashboardInfo.createdAt}</span></div>
            <div>数据源：<span className="text-slate-800 font-bold">{mockDashboardInfo.dataSource}</span></div>
          </div>
        </div>

        {/* Checkpoint Steps Progress Ribbon */}
        <div className="bg-white p-5 rounded-2xl border border-slate-200/80 shadow-xs mb-5">
          <div className="flex items-center gap-2 text-xs font-bold text-slate-500 uppercase tracking-widest mb-4">
            <Layers className="w-4 h-4 text-blue-600" />
            <span>Agent 执行进度</span>
          </div>
          
          {/* Horizontal timeline chart */}
          <div className="grid grid-cols-7 gap-1 relative pt-2 pb-1.5 px-3">
            {/* Background line connector */}
            <div className="absolute top-[22px] left-8 right-8 h-1 bg-slate-100 z-0"></div>
            
            {steps.map((st, i) => {
              const isComp = st.status === 'completed';
              const isRun = st.status === 'running';
              const isPend = st.status === 'pending';
              
              return (
                <div key={st.id} className="flex flex-col items-center relative z-10 text-center">
                  <div className={`w-[26px] h-[26px] rounded-full font-bold text-xs flex items-center justify-center transition-all ${
                    isComp 
                      ? 'bg-emerald-500 text-white shadow-xs' 
                      : isRun 
                        ? 'bg-blue-600 text-white shadow-md animate-pulse border-2 border-blue-200' 
                        : 'bg-slate-100 text-slate-400 border border-slate-200'
                  }`}>
                    {isComp ? <Check className="w-3.5 h-3.5 stroke-[3px]" /> : st.id}
                  </div>
                  <span className={`text-[11px] font-bold mt-2 truncate max-w-[80px] ${isRun ? 'text-blue-600 font-extrabold' : isComp ? 'text-slate-700' : 'text-slate-400'}`}>
                    {st.name}
                  </span>
                </div>
              );
            })}
          </div>

          <div className="mt-4 pt-3.5 border-t border-slate-100 flex items-center gap-2.5 text-xs text-blue-600 font-bold bg-blue-50/50 px-4 py-2.5 rounded-xl border border-blue-100/50">
            <span className="w-2 h-2 rounded-full bg-blue-600 animate-ping"></span>
            <span>正在分析数据结果并生成可视化图表...</span>
          </div>
        </div>

        {/* 5 core metrics grid */}
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3.5 mb-5">
          {mockDashboardInfo.metrics.map((met, idx) => {
            const isDown = met.change.startsWith('↓');
            return (
              <div key={idx} className="bg-white p-4 rounded-xl border border-slate-200/80 shadow-xs flex flex-col justify-between">
                <span className="text-[11px] text-slate-400 font-bold tracking-tight mb-2.5 block truncate">
                  {met.label}
                </span>
                <div className="mb-2">
                  <span className="text-xl font-black text-slate-800 tracking-tight block">
                    {met.value}
                  </span>
                </div>
                <div className="flex items-center gap-1.5 mt-1.5">
                  <span className={`inline-flex items-center gap-0.5 text-[10px] font-extrabold px-1.5 py-0.5 rounded-full ${
                    isDown 
                      ? 'bg-rose-50 text-rose-600 border border-rose-100/80' 
                      : 'bg-emerald-50 text-emerald-600 border border-emerald-100/80'
                  }`}>
                    {isDown ? <ArrowDown className="w-2.5 h-2.5" /> : <ArrowUp className="w-2.5 h-2.5" />}
                    <span>{met.change}</span>
                  </span>
                  <span className="text-[10px] text-slate-400 font-semibold">{met.comparedTo}</span>
                </div>
              </div>
            );
          })}
        </div>

        {/* Interactive Charts Panels */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-5">
          {/* Chart 1: 每日销售额 Bar Chart */}
          <div className="bg-white p-5 rounded-2xl border border-slate-200/80 shadow-xs flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-xs font-bold text-slate-800 uppercase tracking-wider">每日销售额 (R$)</h3>
              <div className="flex items-center gap-2 text-[10px] text-slate-500 font-bold">
                <span className="w-3 h-3 bg-blue-600 rounded"></span>
                <span>销售额 (R$)</span>
              </div>
            </div>
            
            {/* Custom SVG Responsive Bar Chart */}
            <div className="w-full h-[220px] pt-4 relative">
              <svg className="w-full h-full" viewBox="0 0 400 200" preserveAspectRatio="none">
                {/* Y-Axis lines */}
                <line x1="30" y1="20" x2="380" y2="20" stroke="#f1f5f9" strokeDasharray="3,3" />
                <line x1="30" y1="70" x2="380" y2="70" stroke="#f1f5f9" strokeDasharray="3,3" />
                <line x1="30" y1="120" x2="380" y2="120" stroke="#f1f5f9" strokeDasharray="3,3" />
                <line x1="30" y1="170" x2="380" y2="170" stroke="#cbd5e1" />

                {/* Y labels */}
                <text x="5" y="24" fill="#94a3b8" className="text-[9px] font-bold font-mono">300K</text>
                <text x="5" y="74" fill="#94a3b8" className="text-[9px] font-bold font-mono">200K</text>
                <text x="5" y="124" fill="#94a3b8" className="text-[9px] font-bold font-mono">100K</text>
                <text x="15" y="174" fill="#94a3b8" className="text-[9px] font-bold font-mono">0</text>

                {/* Bars - 05-13 to 05-19 */}
                {/* Values scaled: 160k, 130k, 220k, 200k, 260k, 250k, 210k */}
                {/* Scaling multiplier: 150/300k = 0.5px per k */}
                {/* rect height: value * 0.5, y: 170 - height */}
                <g className="cursor-pointer">
                  {/* Bar 1 */}
                  <rect x="55" y="90" width="18" height="80" fill="#2563eb" rx="2" className="hover:fill-blue-700 transition-colors" />
                  <text x="64" y="185" fill="#64748b" className="text-[9px] font-bold" textAnchor="middle">05-13</text>
                  <text x="64" y="84" fill="#1e293b" className="text-[9px] font-bold text-center" textAnchor="middle">160K</text>

                  {/* Bar 2 */}
                  <rect x="105" y="105" width="18" height="65" fill="#2563eb" rx="2" className="hover:fill-blue-700 transition-colors" />
                  <text x="114" y="185" fill="#64748b" className="text-[9px] font-bold" textAnchor="middle">05-14</text>
                  <text x="114" y="99" fill="#1e293b" className="text-[9px] font-bold" textAnchor="middle">130K</text>

                  {/* Bar 3 */}
                  <rect x="155" y="60" width="18" height="110" fill="#2563eb" rx="2" className="hover:fill-blue-700 transition-colors" />
                  <text x="164" y="185" fill="#64748b" className="text-[9px] font-bold" textAnchor="middle">05-15</text>
                  <text x="164" y="54" fill="#1e293b" className="text-[9px] font-bold" textAnchor="middle">220K</text>

                  {/* Bar 4 */}
                  <rect x="205" y="70" width="18" height="100" fill="#2563eb" rx="2" className="hover:fill-blue-700 transition-colors" />
                  <text x="214" y="185" fill="#64748b" className="text-[9px] font-bold" textAnchor="middle">05-16</text>
                  <text x="214" y="64" fill="#1e293b" className="text-[9px] font-bold" textAnchor="middle">200K</text>

                  {/* Bar 5 */}
                  <rect x="255" y="40" width="18" height="130" fill="#2563eb" rx="2" className="hover:fill-blue-700 transition-colors" />
                  <text x="264" y="185" fill="#64748b" className="text-[9px] font-bold" textAnchor="middle">05-17</text>
                  <text x="264" y="34" fill="#1e293b" className="text-[9px] font-bold" textAnchor="middle">260K</text>

                  {/* Bar 6 */}
                  <rect x="305" y="45" width="18" height="125" fill="#2563eb" rx="2" className="hover:fill-blue-700 transition-colors" />
                  <text x="314" y="185" fill="#64748b" className="text-[9px] font-bold" textAnchor="middle">05-18</text>
                  <text x="314" y="39" fill="#1e293b" className="text-[9px] font-bold" textAnchor="middle">250K</text>

                  {/* Bar 7 */}
                  <rect x="355" y="65" width="18" height="105" fill="#2563eb" rx="2" className="hover:fill-blue-700 transition-colors" />
                  <text x="364" y="185" fill="#64748b" className="text-[9px] font-bold" textAnchor="middle">05-19</text>
                  <text x="364" y="59" fill="#1e293b" className="text-[9px] font-bold" textAnchor="middle">210K</text>
                </g>
              </svg>
            </div>
          </div>

          {/* Chart 2: 销售额趋势 Dual Line Chart */}
          <div className="bg-white p-5 rounded-2xl border border-slate-200/80 shadow-xs flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-xs font-bold text-slate-800 uppercase tracking-wider">销售额趋势 (R$)</h3>
              <div className="flex items-center gap-4 text-[10px] text-slate-500 font-bold">
                <div className="flex items-center gap-1">
                  <span className="w-3 h-0.5 bg-blue-600 block"></span>
                  <span>本月</span>
                </div>
                <div className="flex items-center gap-1">
                  <span className="w-3 h-0.5 bg-emerald-500 block"></span>
                  <span>上月</span>
                </div>
              </div>
            </div>

            {/* Custom SVG Dual Line Chart */}
            <div className="w-full h-[220px] pt-4 relative">
              <svg className="w-full h-full" viewBox="0 0 400 200" preserveAspectRatio="none">
                {/* Y-axis lines */}
                <line x1="30" y1="20" x2="380" y2="20" stroke="#f1f5f9" strokeDasharray="3,3" />
                <line x1="30" y1="70" x2="380" y2="70" stroke="#f1f5f9" strokeDasharray="3,3" />
                <line x1="30" y1="120" x2="380" y2="120" stroke="#f1f5f9" strokeDasharray="3,3" />
                <line x1="30" y1="170" x2="380" y2="170" stroke="#cbd5e1" />

                {/* Y labels */}
                <text x="5" y="24" fill="#94a3b8" className="text-[9px] font-bold font-mono">300K</text>
                <text x="5" y="74" fill="#94a3b8" className="text-[9px] font-bold font-mono">200K</text>
                <text x="5" y="124" fill="#94a3b8" className="text-[9px] font-bold font-mono">100K</text>
                <text x="15" y="174" fill="#94a3b8" className="text-[9px] font-bold font-mono">0</text>

                {/* Dates nodes x: 64, 114, 164, 214, 264, 314, 364 */}
                {/* 本月 values (blue): 200k, 60k, 120k, 80k, 180k, 110k, 240k */}
                {/* Scaling: value * 0.5. Y value: 170 - value*0.5 */}
                {/* Y: 200k->70, 60k->140, 120k->110, 80k->130, 180k->80, 110k->115, 240k->50 */}
                <path d="M 64,70 L 114,140 L 164,110 L 214,130 L 264,80 L 314,115 L 364,50" 
                      fill="none" stroke="#2563eb" strokeWidth="2.5" className="shadow-sm" />
                
                {/* 上月 values (green): 140k -> 100, 100k -> 120, 80k -> 130, 160k -> 90, 100k -> 120, 130k -> 105, 150k -> 95 */}
                <path d="M 64,100 L 114,120 L 164,130 L 214,90 L 264,120 L 314,105 L 364,95" 
                      fill="none" stroke="#10b981" strokeWidth="2" strokeDasharray="3,1" />

                {/* Nodes - Local Month */}
                <g fill="#2563eb" className="cursor-pointer">
                  <circle cx="64" cy="70" r="3.5" stroke="white" strokeWidth="1" />
                  <circle cx="114" cy="140" r="3.5" stroke="white" strokeWidth="1" />
                  <circle cx="164" cy="110" r="3.5" stroke="white" strokeWidth="1" />
                  <circle cx="214" cy="130" r="3.5" stroke="white" strokeWidth="1" />
                  <circle cx="264" cy="80" r="3.5" stroke="white" strokeWidth="1" />
                  <circle cx="314" cy="115" r="3.5" stroke="white" strokeWidth="1" />
                  <circle cx="364" cy="50" r="3.5" stroke="white" strokeWidth="1" />
                </g>

                {/* Nodes - Prior Month */}
                <g fill="#10b981">
                  <circle cx="64" cy="100" r="3" stroke="white" strokeWidth="1" />
                  <circle cx="114" cy="120" r="3" stroke="white" strokeWidth="1" />
                  <circle cx="164" cy="130" r="3" stroke="white" strokeWidth="1" />
                  <circle cx="214" cy="90" r="3" stroke="white" strokeWidth="1" />
                  <circle cx="264" cy="120" r="3" stroke="white" strokeWidth="1" />
                  <circle cx="314" cy="105" r="3" stroke="white" strokeWidth="1" />
                  <circle cx="364" cy="95" r="3" stroke="white" strokeWidth="1" />
                </g>

                {/* XAxis categories */}
                <text x="64" y="185" fill="#64748b" className="text-[9px] font-bold" textAnchor="middle">05-13</text>
                <text x="114" y="185" fill="#64748b" className="text-[9px] font-bold" textAnchor="middle">05-14</text>
                <text x="164" y="185" fill="#64748b" className="text-[9px] font-bold" textAnchor="middle">05-15</text>
                <text x="214" y="185" fill="#64748b" className="text-[9px] font-bold" textAnchor="middle">05-16</text>
                <text x="264" y="185" fill="#64748b" className="text-[9px] font-bold" textAnchor="middle">05-17</text>
                <text x="314" y="185" fill="#64748b" className="text-[9px] font-bold" textAnchor="middle">05-18</text>
                <text x="364" y="185" fill="#64748b" className="text-[9px] font-bold" textAnchor="middle">05-19</text>
              </svg>
            </div>
          </div>
        </div>

        {/* Lower Grid: Top 10 categories Table vs Key findings */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
          
          {/* Top 10 category performance (Left 7 Columns) */}
          <div className="bg-white rounded-2xl border border-slate-200/80 shadow-xs p-5 lg:col-span-7 flex flex-col justify-between">
            <div>
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-xs font-bold text-slate-800 uppercase tracking-wider flex items-center gap-1.5">
                  <TableIcon className="w-4.5 h-4.5 text-blue-600" />
                  <span>Top 10 品类 (按销售额)</span>
                </h3>
                <span className="text-[10px] text-slate-400 font-semibold bg-slate-100 px-2 py-0.5 rounded">
                  巴西雷亚尔 (R$)
                </span>
              </div>

              {/* Table details */}
              <div className="overflow-x-auto">
                <Table className="text-left text-xs font-medium text-slate-600">
                  <TableHeader>
                    <TableRow className="border-b border-slate-100 text-[10px] font-bold uppercase text-slate-400 hover:bg-transparent">
                      <TableHead className="py-2.5 pl-2 text-[10px] font-bold uppercase text-slate-400">排名</TableHead>
                      <TableHead className="py-2.5 text-[10px] font-bold uppercase text-slate-400">品类</TableHead>
                      <TableHead className="py-2.5 text-right text-[10px] font-bold uppercase text-slate-400">销售额 (R$)</TableHead>
                      <TableHead className="py-2.5 text-right text-[10px] font-bold uppercase text-slate-400">占比</TableHead>
                      <TableHead className="py-2.5 pr-2 text-center text-[10px] font-bold uppercase text-slate-400">趋势</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {mockDashboardInfo.topCategories.map((cat, i) => (
                      <TableRow key={i} className="border-b border-slate-50 transition-colors last:border-b-0 hover:bg-slate-50/50">
                        <TableCell className="py-2.5 pl-2">
                          <span className={`w-5 h-5 rounded flex items-center justify-center font-bold text-[10px] ${
                            i === 0 
                              ? 'bg-amber-100 text-amber-700' 
                              : i === 1 
                                ? 'bg-slate-100 text-slate-700' 
                                : i === 2 
                                  ? 'bg-orange-50 text-orange-700' 
                                  : 'text-slate-400'
                          }`}>
                            {cat.rank}
                          </span>
                        </TableCell>
                        <TableCell className="py-2.5 font-bold text-slate-800">{cat.category}</TableCell>
                        <TableCell className="py-2.5 text-right font-mono font-bold text-slate-700">{cat.sales}</TableCell>
                        <TableCell className="py-2.5 text-right font-mono text-slate-500">{cat.ratio}</TableCell>
                        <TableCell className="py-2.5 pr-2 text-center">
                          <span className="text-emerald-500 font-bold">▲</span>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            </div>
            <div className="mt-4 text-center">
              <Button 
                onClick={() => alert('正在加载更多品类穿透明细报表...')}
                variant="link"
                className="h-auto p-0 text-xs font-bold text-blue-600"
              >
                查看更多 ›
              </Button>
            </div>
          </div>

          {/* Right Text Analytics Findings & Scope (Right 5 columns) */}
          <div className="lg:col-span-5 flex flex-col gap-4">
            
            {/* Key findings card */}
            <div className="bg-white p-5 rounded-2xl border border-slate-200/80 shadow-xs flex-1">
              <h3 className="text-xs font-bold text-slate-800 uppercase tracking-wider mb-4 pb-1 border-b border-slate-100">
                关键发现
              </h3>
              <ul className="space-y-3">
                {mockDashboardInfo.keyFindings.map((fd, index) => (
                  <li key={index} className="flex gap-2.5 items-start text-xs font-medium text-slate-600 leading-relaxed">
                    <span className="w-1.5 h-1.5 bg-blue-600 rounded-full shrink-0 mt-1.5"></span>
                    <span>{fd}</span>
                  </li>
                ))}
              </ul>

              {/* Data Scope indicators */}
              <div className="mt-5 pt-4 border-t border-slate-100">
                <span className="block text-[10px] font-bold text-slate-400 uppercase mb-2">
                  引用数据源及实体
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {['orders', 'order_items', 'products', 'customers', 'payments'].map((tName, tIdx) => (
                    <span key={tIdx} className="bg-slate-50 border border-slate-200 rounded px-2 py-0.5 text-[9px] text-slate-500 font-bold font-mono">
                      {tName}
                    </span>
                  ))}
                </div>
              </div>
            </div>

            {/* Constraints rules definitions */}
            <div className="bg-slate-900 text-slate-200 p-5 rounded-2xl border border-slate-850 shadow-md">
              <h3 className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-3.5 flex items-center gap-1.5">
                <Info className="w-4 h-4 text-slate-400" />
                <span>口径限制说明</span>
              </h3>
              <ul className="space-y-2.5">
                {mockDashboardInfo.limitInstructions.map((li, index) => (
                  <li key={index} className="flex items-center gap-2 text-[11px] font-semibold text-slate-300">
                    <span className="text-[9px] text-blue-400">✓</span>
                    <span>{li}</span>
                  </li>
                ))}
              </ul>
            </div>

          </div>

        </div>

      </div>

      {/* 2. Interactive Right Detailing Drawer Panel (Width: 320px) */}
      <div className="w-[340px] bg-white h-screen flex flex-col shrink-0 select-none border-l border-[#e2e8f0]">
        
        {/* Drawer Header Tabs */}
        <div className="p-4 pb-1.5 border-b border-slate-100 flex items-center justify-between">
          <h3 className="text-sm font-bold text-slate-800">执行详情</h3>
          <Button 
            onClick={() => alert('执行详情为侧边辅助窗口，保持常开以便随时监控分析逻辑。')}
            variant="ghost"
            className="h-8 w-8 rounded p-0 text-slate-400 hover:bg-slate-50 hover:text-slate-600"
          >
            <X className="w-4.5 h-4.5" />
          </Button>
        </div>

        {/* Dynamic Navigation Selectors */}
        <div className="px-3 py-1 flex border-b border-slate-100 bg-slate-50/50">
          {(['steps', 'sql', 'sources', 'audit'] as const).map((tab) => {
            const labels = { steps: 'Timeline', sql: 'SQL', sources: 'Sources', audit: 'Audit' };
            const isActive = activeTab === tab;
            return (
              <Button
                key={tab}
                onClick={() => setActiveTab(tab)}
                variant="ghost"
                className={`h-auto flex-1 rounded-none border-b-2 py-2 text-center text-[11px] font-bold transition-all hover:bg-transparent ${
                  isActive 
                    ? 'border-blue-600 text-blue-600 font-extrabold' 
                    : 'border-transparent text-slate-400 hover:text-slate-700'
                }`}
              >
                {labels[tab]}
              </Button>
            );
          })}
        </div>

        {/* Tab Canvas Scroller */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          
          {/* TAB 1: STEPS / TIMELINE */}
          {activeTab === 'steps' && (
            <div className="space-y-4 text-xs">
              {/* Timeline Items */}
              <div className="relative pl-4 space-y-4 border-l border-slate-100 ml-2">
                {steps.map((st) => {
                  const isComp = st.status === 'completed';
                  const isRun = st.status === 'running';
                  return (
                    <div key={st.id} className="relative group text-left">
                      {/* Timeline dot */}
                      <span className={`absolute -left-[21px] top-0.5 w-[14px] h-[14px] rounded-full border-2 flex items-center justify-center ${
                        isComp 
                          ? 'bg-emerald-500 border-emerald-500 text-white' 
                          : isRun 
                            ? 'bg-blue-600 border-blue-600 animate-ping' 
                            : 'bg-white border-slate-200'
                      }`}>
                        {isComp && <Check className="w-2.5 h-2.5 text-white" />}
                      </span>
                      
                      {/* Timeline Content */}
                      <div className="flex items-center justify-between gap-1 mb-1 font-bold">
                        <span className={isRun ? 'text-blue-600 font-extrabold' : 'text-slate-700'}>{st.name}</span>
                        <span className="font-mono text-[10px] text-slate-400 bg-slate-50 px-1 rounded">{st.duration}</span>
                      </div>
                      <p className="text-[10px] text-slate-400 font-semibold leading-relaxed">
                        {st.description}
                      </p>
                    </div>
                  );
                })}
              </div>

              {/* Pending Resolution detail card */}
              <div className="bg-blue-50/50 border border-blue-100 rounded-xl p-4 mt-6 text-left">
                <h4 className="text-xs font-bold text-blue-900 mb-1 flex items-center justify-between">
                  <span>当前步骤详情</span>
                  <span className="text-[10px] bg-blue-100 text-blue-600 font-extrabold px-1.5 py-0.5 rounded-full">65%</span>
                </h4>
                <p className="text-[10px] text-blue-600 font-semibold mb-3 leading-relaxed">
                  正在对查询结果进行多维分组、指标计算，并同步输出每日销售趋势数据...
                </p>

                {/* Progress bar scale */}
                <div className="w-full h-1.5 bg-slate-200 rounded-full overflow-hidden mb-4">
                  <div className="h-full bg-blue-600 rounded-full animate-pulse transition-all" style={{ width: '65%' }}></div>
                </div>

                <div className="space-y-1.5 text-[10px] text-slate-500">
                  <div className="flex justify-between">
                    <span>预计剩余时间</span>
                    <span className="font-bold font-mono text-slate-800">00:18</span>
                  </div>
                  <div className="flex justify-between">
                    <span>启动运行时间</span>
                    <span className="font-bold font-mono text-slate-800">2024-05-19 10:30:52</span>
                  </div>
                </div>

                {/* Cancel analysis button */}
                <Button 
                  onClick={() => alert('分析已成功取消，状态回到新建会话')}
                  variant="outline"
                  className="mt-4 w-full rounded-lg border-rose-200 bg-white py-1.5 text-xs font-bold text-rose-600 hover:bg-rose-50/50 hover:text-rose-700"
                >
                  取消分析
                </Button>
              </div>
            </div>
          )}

          {/* TAB 2: SQL INTERPRETER */}
          {activeTab === 'sql' && (
            <div className="space-y-4 text-xs text-left">
              {/* Generate SQL Display */}
              <div className="bg-slate-900 text-slate-300 p-3 rounded-xl border border-slate-800 shadow-sm relative">
                <div className="flex justify-between items-center mb-2.5">
                  <span className="text-[10px] text-slate-400 font-bold tracking-widest uppercase">PostgreSQL SQL</span>
                  <Button 
                    onClick={handleCopy}
                    variant="ghost"
                    className="h-7 rounded bg-slate-800 p-1 text-[9px] font-bold text-slate-400 hover:bg-slate-700 hover:text-slate-200"
                  >
                    <Copy className="w-3 h-3" />
                    <span>{copied ? '已复制' : '复制'}</span>
                  </Button>
                </div>
                
                {/* Simulated dynamic code view */}
                <pre className="font-mono text-[9px] overflow-x-auto select-text leading-relaxed text-blue-400 p-1.5 max-h-[220px]">
                  {generatedSQLContent}
                </pre>
              </div>

              {/* Collapsible Optimizations suggestions */}
              <div className="space-y-2">
                <h4 className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">SQL 优化建议</h4>
                <div className="space-y-1.5">
                  {sqlAdvices.map((advice, k) => (
                    <div key={k} className="p-2.5 bg-amber-50/50 text-[#854d0e] border border-amber-100 rounded-lg flex items-start gap-2 text-[10px] leading-relaxed">
                      <span className="font-bold shrink-0 text-amber-500 bg-amber-100 w-4.5 h-4.5 rounded-full flex items-center justify-center text-[9px]">
                        {k+1}
                      </span>
                      <span>{advice}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Safety audits table */}
              <div className="space-y-2">
                <h4 className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">安全检查结果</h4>
                <div className="border border-slate-100 rounded-lg overflow-hidden bg-white">
                  {safetyChecks.map((sc, scIdx) => (
                    <div key={scIdx} className="flex justify-between items-center p-2 border-b border-slate-50 last:border-b-0 text-[10px]">
                      <span className="text-slate-600 font-medium">{sc.label}</span>
                      <Badge className="bg-emerald-50 px-1.5 py-0.5 text-[9px] font-extrabold text-emerald-600">通过</Badge>
                    </div>
                  ))}
                </div>
              </div>

              {/* Execution plane details list */}
              <div className="space-y-2">
                <h4 className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">分析执行计划 (MOCK)</h4>
                <div className="border border-slate-100 rounded-lg overflow-hidden bg-slate-50/50">
                  <Table className="text-[9px] text-slate-500">
                    <TableHeader>
                      <TableRow className="border-b border-slate-200 bg-slate-100 hover:bg-slate-100">
                        <TableHead className="p-1.5 text-left text-[9px]">阶段</TableHead>
                        <TableHead className="p-1.5 text-left text-[9px]">执行步骤</TableHead>
                        <TableHead className="p-1.5 text-right text-[9px]">预估行数</TableHead>
                        <TableHead className="p-1.5 text-right text-[9px]">用时</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {executionPlans.map((ep) => (
                        <TableRow key={ep.stage} className="border-b border-slate-200 last:border-b-0">
                          <TableCell className="p-1.5 font-bold text-slate-800">{ep.stage}</TableCell>
                          <TableCell className="max-w-[110px] truncate p-1.5 font-mono text-slate-600">{ep.action}</TableCell>
                          <TableCell className="p-1.5 text-right font-mono">{ep.rows}</TableCell>
                          <TableCell className="p-1.5 text-right font-mono font-bold text-blue-600">{ep.cost}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </div>
            </div>
          )}

          {/* TAB 3: SOURCES / INTEGRATED ENTITIES */}
          {activeTab === 'sources' && (
            <div className="space-y-4 text-xs text-left">
              <h4 className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">引用关联表</h4>
              <div className="space-y-2">
                {databaseTables.map((db, dbIdx) => {
                  const isSel = selectedTable === db.name;
                  return (
                    <div 
                      key={dbIdx}
                      onClick={() => setSelectedTable(db.name)}
                      className={`p-2.5 rounded-xl border border-slate-200/80 cursor-pointer transition-all ${
                        isSel 
                          ? 'bg-blue-50/70 border-blue-400 shadow-xs' 
                          : 'bg-white hover:bg-slate-50/50'
                      }`}
                    >
                      <div className="flex justify-between items-center mb-1">
                        <div className="flex items-center gap-1.5">
                          <Database className="w-3.5 h-3.5 text-slate-400" />
                          <span className="font-extrabold text-slate-800">{db.name}</span>
                          <span className="text-[10px] text-slate-400">({db.nick})</span>
                        </div>
                        <Badge className="bg-emerald-50 px-1.5 py-0.5 text-[9px] font-bold text-emerald-600">
                          {db.status}
                        </Badge>
                      </div>
                      <p className="text-[10px] text-slate-500 leading-relaxed truncate-3-lines mb-1 font-medium">
                        {db.desc}
                      </p>
                      <div className="text-[9px] text-blue-600 font-bold">引用本表 {db.cols} 项关键物理字段</div>
                    </div>
                  );
                })}
              </div>

              {/* Dynamic referenced column details */}
              <div className="space-y-2">
                <div className="flex justify-between items-center">
                  <h4 className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">引用字段索引列表</h4>
                  <span className="text-[9px] text-slate-400 font-mono">共选中表关联 10 字段</span>
                </div>
                <div className="border border-slate-100 rounded-lg overflow-hidden bg-white">
                  <Table className="text-[10px] text-slate-600">
                    <TableHeader>
                      <TableRow className="border-b border-slate-100 bg-slate-50 text-[9px] font-bold uppercase text-slate-400 hover:bg-slate-50">
                        <TableHead className="p-2 text-left text-[9px] font-bold uppercase text-slate-400">物理字段</TableHead>
                        <TableHead className="p-2 text-left text-[9px] font-bold uppercase text-slate-400">所属表</TableHead>
                        <TableHead className="p-2 text-left text-[9px] font-bold uppercase text-slate-400">物理类型</TableHead>
                        <TableHead className="p-2 text-center text-[9px] font-bold uppercase text-slate-400">状态</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {fieldList.map((fl, flIdx) => (
                        <TableRow key={flIdx} className="border-b border-slate-50 last:border-b-0 hover:bg-slate-50/30">
                          <TableCell className="p-2 font-mono font-bold text-slate-800">{fl.field}</TableCell>
                          <TableCell className="p-2 font-mono text-slate-400">{fl.table}</TableCell>
                          <TableCell className="p-2 font-mono text-slate-400">{fl.type}</TableCell>
                          <TableCell className="p-2 text-center">
                            <span className="text-emerald-500 font-bold text-[9px]">✔</span>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </div>
            </div>
          )}

          {/* TAB 4: AUDIT SECURITY PANEL */}
          {activeTab === 'audit' && (
            <div className="space-y-4 text-xs text-left">
              {/* Risk review indicator */}
              <div className="bg-emerald-50/50 border border-emerald-100 p-3 rounded-xl flex items-center justify-between text-emerald-800">
                <div className="flex items-center gap-2">
                  <ShieldCheck className="w-5 h-5 text-emerald-500 fill-emerald-100" />
                  <div>
                    <div className="text-xs font-bold text-emerald-900">执行风险评估：低风险</div>
                    <div className="text-[9px] text-emerald-600">本次分析通过全部五项内置安全模型审查</div>
                  </div>
                </div>
                <Badge className="bg-emerald-100 px-2 py-0.5 text-[10px] font-bold text-emerald-800">
                  通过
                </Badge>
              </div>

              {/* Security checkpoint list */}
              <div className="space-y-2">
                <h4 className="text-[10px] font-bold text-slate-400 uppercase tracking-widest">安全规则审查明细</h4>
                <div className="space-y-2">
                  {[
                    { label: 'SQL 类型检测', value: 'SELECT 只读语义型查询', status: '通过' },
                    { label: '写操作检测 (INSERT/UPDATE/DELETE)', value: '未检测到任何变更写入行为', status: '通过' },
                    { label: '删除行为审查 (DROP/TRUNCATE)', value: '未检测到任何高风险清除操作', status: '通过' },
                    { label: '敏感字段模糊泄露检测 (信用资产、明文哈希等)', value: '未命中屏蔽字典或白名单字段', status: '通过' },
                    { label: '超限结果防护 (LIMIT 1000)', value: '已自动启用硬编码约束限行', status: '通过' },
                  ].map((aud, audIdx) => (
                    <div key={audIdx} className="p-2.5 bg-slate-50 border border-slate-200 rounded-lg text-[10px] space-y-1">
                      <div className="flex justify-between items-center font-bold">
                        <span className="text-slate-800">{aud.label}</span>
                        <Badge className="bg-emerald-50 px-1.5 py-0.5 text-[9px] font-extrabold text-emerald-600">通过</Badge>
                      </div>
                      <p className="text-slate-400 font-mono text-[9px]">{aud.value}</p>
                    </div>
                  ))}
                </div>
              </div>

              {/* Permission level review */}
              <div className="space-y-2">
                <h4 className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">权限等级校验</h4>
                <div className="border border-slate-100 rounded-lg overflow-hidden bg-white text-[10px]">
                  {[
                    { label: '数据源物理连接权限', status: '已授权' },
                    { label: '库表实体只读可检索权限', status: '已授权' },
                    { label: '业务语义层指标映射权限', status: '已授权' },
                    { label: 'SQL 请求计算强度权限', status: '已授权' },
                    { label: '临时执行超时限制设置 (60s)', status: '已配置' },
                  ].map((pm, pmIdx) => (
                    <div key={pmIdx} className="flex justify-between p-2 border-b border-slate-50 last:border-b-0 font-medium">
                      <span className="text-slate-600">{pm.label}</span>
                      <span className="text-emerald-600 font-bold">{pm.status}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* History auditing log trail */}
              <div className="space-y-2">
                <h4 className="text-[10px] font-bold text-slate-400 uppercase tracking-wider">生命周期审计路径 (Audit Trails)</h4>
                <div className="border border-slate-100 rounded-lg overflow-hidden bg-slate-50/50">
                  <Table className="text-[9px] text-slate-500">
                    <TableHeader>
                      <TableRow className="border-b border-slate-200 bg-slate-100 hover:bg-slate-100">
                        <TableHead className="p-1.5 text-left text-[9px]">审核时间</TableHead>
                        <TableHead className="p-1.5 text-left text-[9px]">结果</TableHead>
                        <TableHead className="p-1.5 text-left text-[9px]">审核类型</TableHead>
                        <TableHead className="p-1.5 text-center text-[9px]">人工核销</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {auditLogs.map((log, i) => (
                        <TableRow key={i} className="border-b border-slate-200 last:border-b-0">
                          <TableCell className="max-w-[80px] truncate p-1.5 font-mono text-slate-600">{log.time}</TableCell>
                          <TableCell className="p-1.5 font-bold text-emerald-600">{log.result}</TableCell>
                          <TableCell className="p-1.5 text-slate-600">{log.type}</TableCell>
                          <TableCell className="p-1.5 text-center font-bold text-slate-400">{log.manVer}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </div>
            </div>
          )}

        </div>
      </div>

    </div>
  );
}
