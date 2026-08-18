"use client";

import React, { useEffect, useState } from 'react';
import { 
  Search, 
  FileText, 
  Table as TableIcon, 
  Network, 
  RefreshCw, 
} from 'lucide-react';
import { apiGet } from '../../lib/api';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

export default function DatasetView() {
  /** 数据集说明页面：展示 Olist 表、字段、查询规则和 Join 元信息。 */

  const [tables, setTables] = useState<any[]>([]);
  const [selectedTableName, setSelectedTableName] = useState('orders');
  const [searchQuery, setSearchQuery] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let ignore = false;

    async function loadDatasets() {
      /** 从后端读取数据集表结构，并默认选中 orders 表。 */

      try {
        setLoading(true);
        const data: any = await apiGet('/api/semantic', { asset_type: 'datasets' });
        if (ignore) return;
        const nextTables = data.datasets ?? [];
        setTables(nextTables);
        setSelectedTableName(nextTables.find((table: any) => table.name === 'orders')?.name ?? nextTables[0]?.name ?? 'orders');
        setError('');
      } catch (err) {
        if (ignore) return;
        setError(err instanceof Error ? err.message : '数据集接口请求失败');
      } finally {
        if (!ignore) setLoading(false);
      }
    }

    loadDatasets();

    return () => {
      ignore = true;
    };
  }, []);

  const currentTable = tables.find(t => t.name === selectedTableName) || tables[0];
  const filteredSidebarRows = tables.filter(table => 
    table.name.toLowerCase().includes(searchQuery.toLowerCase()) || 
    table.description.toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <div className="flex-1 flex overflow-hidden bg-slate-50 relative font-sans font-medium text-slate-800">
      
      {/* 1. Main Left Table Directory (Width: 240px) */}
      <div className="w-[240px] bg-white border-r border-[#e2e8f0] h-screen flex flex-col shrink-0 select-none">
        <div className="p-4 border-b border-slate-100">
          <h3 className="text-xs font-bold text-slate-400 uppercase tracking-wider mb-2 text-left">数据表列表</h3>
          <div className="relative">
            <Input
              type="text"
              placeholder="搜索数据库表..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="h-8 rounded-lg border-slate-200 bg-slate-50 pl-8 pr-2.5 text-[11px] font-semibold focus-visible:ring-blue-500"
            />
            <Search className="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-2.5" />
          </div>
        </div>

        {/* Directory Scroller */}
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {filteredSidebarRows.map((row) => {
            const isSel = selectedTableName === row.name;
            return (
              <Button
                key={row.name}
                onClick={() => setSelectedTableName(row.name)}
                variant="ghost"
                className={`h-auto w-full justify-start rounded-lg px-3 py-2 text-left font-sans transition-all ${
                  isSel 
                    ? 'bg-blue-50 text-blue-600 font-extrabold border-l-4 border-blue-600 shadow-xs' 
                    : 'text-slate-600 hover:bg-slate-200/40 hover:text-slate-800'
                }`}
              >
                <span className="font-mono text-xs font-bold">{row.name}</span>
                <span className="text-[10px] text-slate-400 mt-0.5 line-clamp-1">{row.description}</span>
              </Button>
            );
          })}
          {filteredSidebarRows.length === 0 && (
            <div className="text-center font-semibold text-[11px] text-slate-400 p-4">没有匹配的数据表</div>
          )}
        </div>
      </div>

      {/* 2. Central Schema Specs view */}
      <div className="flex-1 overflow-y-auto px-6 py-6 border-r border-[#e2e8f0]">
        
        {/* Header Summary */}
        <div className="mb-6 text-left">
          <h2 className="text-xl font-extrabold text-slate-800 tracking-tight">数据集说明</h2>
          <p className="text-xs text-slate-500 font-semibold mt-1">
            当前数据库共包含 9 张离散实体表。管理库表间的主外键关联、索引，以备 Agent 执行大范围多表多JOIN查询。
          </p>
        </div>

        {/* Stats Blocks */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
          <Card className="border-slate-200/80 bg-white p-4 shadow-xs">
            <CardContent className="flex items-center gap-3 p-0">
            <div className="p-2.5 rounded-xl bg-blue-50 text-blue-600 shrink-0">
              <TableIcon className="w-5 h-5" />
            </div>
            <div>
              <span className="block text-[10px] text-slate-400 font-bold uppercase tracking-wider">可用数据表</span>
              <span className="text-xl font-black text-slate-800 font-mono tracking-tight">{tables.length} 张</span>
            </div>
            </CardContent>
          </Card>
          <Card className="border-slate-200/80 bg-white p-4 shadow-xs">
            <CardContent className="flex items-center gap-3 p-0">
            <div className="p-2.5 rounded-xl bg-indigo-50 text-indigo-600 shrink-0">
              <FileText className="w-5 h-5" />
            </div>
            <div>
              <span className="block text-[10px] text-slate-400 font-bold uppercase tracking-wider">可查询字段</span>
              <span className="text-xl font-black text-slate-800 font-mono tracking-tight font-sans">{tables.reduce((total, table) => total + (table.fields?.length ?? 0), 0)} 个</span>
            </div>
            </CardContent>
          </Card>
          <Card className="border-slate-200/80 bg-white p-4 shadow-xs">
            <CardContent className="flex items-center gap-3 p-0">
            <div className="p-1.5 px-2 rounded-xl bg-purple-50 text-purple-600 shrink-0">
              <Network className="w-5 h-5 animate-pulse" />
            </div>
            <div>
              <span className="block text-[10px] text-slate-400 font-bold uppercase tracking-wider">常用 JOIN 路径</span>
              <span className="text-xl font-black text-slate-800 font-mono tracking-tight font-sans">{tables.reduce((total, table) => total + (table.dependent_relations?.length ?? 0), 0)} 条</span>
            </div>
            </CardContent>
          </Card>
          <Card className="border-slate-200/80 bg-white p-4 shadow-xs">
            <CardContent className="flex items-center gap-3 p-0">
            <div className="p-2.5 rounded-xl bg-emerald-50 text-emerald-600 shrink-0">
              <RefreshCw className="w-5 h-5" />
            </div>
            <div>
              <span className="block text-[10px] text-slate-400 font-bold uppercase tracking-wider">最近扫描时间</span>
              <span className="text-sm font-black text-slate-700 tracking-tight font-sans">2026-05-20</span>
            </div>
            </CardContent>
          </Card>
        </div>

        {/* Selected Table specs cards */}
        {error && (
          <Alert className="mb-4 border-rose-100 bg-rose-50 text-rose-600">
            <AlertDescription className="text-xs font-bold">数据集接口加载失败：{error}</AlertDescription>
          </Alert>
        )}
        {loading && (
          <Alert className="mb-4 border-blue-100 bg-blue-50 text-blue-600">
            <AlertDescription className="text-xs font-bold">正在从 FastAPI 加载 Olist 数据集说明...</AlertDescription>
          </Alert>
        )}
        {currentTable && (
        <Card className="mb-5 border-slate-200/80 bg-white p-5 text-left shadow-xs">
          <CardContent className="p-0">
            <div className="flex items-center justify-between border-b border-slate-100 pb-3 mb-4">
            <div className="flex items-center gap-2.5">
              <span className="text-lg font-black font-mono text-slate-800">{currentTable.name}</span>
              <Badge className="border border-emerald-100/50 bg-emerald-50 px-2.5 py-0.5 text-[10px] font-black text-emerald-600">
                允许 Agent 查询
              </Badge>
            </div>
            <span className="text-xs font-bold text-slate-400">含有 {currentTable.fields?.length || 5} 核心结构列表</span>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-5 text-xs text-slate-500 font-semibold">
            <div className="p-3 bg-slate-50/50 rounded-lg border border-slate-100">
              <span>物理主键 Primary Key</span>
              <span className="block font-mono text-slate-800 font-bold mt-1 text-sm">{currentTable.primary_key || '未配置'}</span>
            </div>
            <div className="p-3 bg-slate-50/50 rounded-lg border border-slate-100">
              <span>常用汇总时间字段</span>
              <span className="block font-mono text-slate-800 font-bold mt-1 text-sm">{currentTable.common_time_field || '未配置'}</span>
            </div>
            <div className="p-3 bg-slate-50/50 rounded-lg border border-slate-100">
              <span>关联数据集库归属</span>
              <span className="block font-mono text-slate-800 font-bold mt-1 text-sm">Olist Ecommerce</span>
            </div>
          </div>

          {/* Fields list table */}
          <div>
            <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-3">
              库字段物理定义 (Schema)
            </span>
            <div className="border border-slate-100 rounded-xl overflow-hidden bg-white text-xs">
              <Table className="text-left text-xs text-slate-650">
                <TableHeader>
                  <TableRow className="border-b border-slate-200 bg-slate-50 text-[9px] font-bold uppercase text-slate-400 hover:bg-slate-50">
                    <TableHead className="px-3 py-2.5 text-[9px] font-bold uppercase text-slate-400">名称 Name</TableHead>
                    <TableHead className="px-3 py-2.5 text-[9px] font-bold uppercase text-slate-400">业务说明 Description</TableHead>
                    <TableHead className="px-3 py-2.5 text-[9px] font-bold uppercase text-slate-400">物理类型 Data Type</TableHead>
                    <TableHead className="px-3 py-2.5 text-center text-[9px] font-bold uppercase text-slate-400">语义建模层状态</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(currentTable.fields || []).map((f: any, i: number) => {
                    const isM = f.status === '已命中语义层';
                    return (
                      <TableRow key={i} className="border-b border-slate-50 last:border-b-0">
                        <TableCell className="px-3 py-2.5 font-mono font-bold text-slate-800">{f.name}</TableCell>
                        <TableCell className="px-3 py-2.5 text-slate-500">{f.description}</TableCell>
                        <TableCell className="px-3 py-2.5 font-mono text-slate-400">{f.type}</TableCell>
                        <TableCell className="px-3 py-2.5 text-center">
                          <Badge className={`px-2 py-0.5 text-[9px] font-bold ${
                            isM 
                              ? 'bg-emerald-50 text-emerald-600 border border-emerald-100' 
                              : 'bg-slate-100 text-slate-400 border border-slate-200'
                          }`}>
                            {f.status}
                          </Badge>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          </div>
          </CardContent>
        </Card>
        )}

        {/* 3. Join Diagram Flow Chart layout */}
        <Card className="border-slate-200/80 bg-white p-5 text-left shadow-xs">
          <CardContent className="p-0">
          <div className="flex items-center justify-between mb-4 border-b border-slate-100 pb-2">
            <h3 className="text-xs font-bold text-slate-800 uppercase tracking-wider flex items-center gap-1.5">
              <Network className="w-4.5 h-4.5 text-blue-600" />
              <span>Olist Ecommerce 关联路径示意图</span>
            </h3>
            <div className="flex gap-4 text-[9px] text-slate-400 uppercase font-black">
              <div className="flex items-center gap-1">
                <span className="w-3 h-0.5 bg-blue-500 block"></span>
                <span>一对一</span>
              </div>
              <div className="flex items-center gap-1">
                <span className="w-3 h-0.5 bg-emerald-500 block"></span>
                <span>一对多</span>
              </div>
              <div className="flex items-center gap-1">
                <span className="w-3 h-0.5 bg-purple-500 block"></span>
                <span>多对一</span>
              </div>
            </div>
          </div>

          {/* SVG Interactive Join Diagram */}
          <div className="w-full bg-slate-50/50 rounded-xl border border-slate-200/50 p-6 flex flex-wrap items-center justify-center min-h-[160px] gap-8 relative overflow-hidden">
            <svg className="absolute inset-0 w-full h-full pointer-events-none" style={{ minWidth: '500px' }}>
              {/* Line 1: orders -> order_items */}
              <path d="M 85,60 C 130,60 130,40 180,40" stroke="#10b981" strokeWidth="2" fill="none" />
              {/* Line 2: orders -> customers */}
              <line x1="85" y1="65" x2="180" y2="100" stroke="#2563eb" strokeWidth="2" />
              {/* Line 3: orders -> payments */}
              <path d="M 85,70 C 130,70 130,150 180,150" stroke="#a855f7" strokeWidth="2" fill="none" />
              {/* Line 4: order_items -> products */}
              <line x1="280" y1="40" x2="350" y2="40" stroke="#10b981" strokeWidth="2" />
            </svg>

            {/* Entity Nodes */}
            <div className="grid grid-flow-col auto-cols-auto gap-x-16 gap-y-4 z-10 relative">
              {/* Root orders node */}
              <div className="flex flex-col gap-4">
                <div className="bg-white border border-blue-500 rounded-lg p-3 w-[110px] text-center shadow-md">
                  <span className="font-mono text-xs font-black block text-blue-600">orders</span>
                  <span className="text-[9px] text-slate-400 italic">订单主表</span>
                </div>
              </div>

              {/* Mapped entities */}
              <div className="flex flex-col gap-4">
                <div className="bg-white border border-emerald-500 rounded-lg p-2.5 w-[110px] text-center shadow">
                  <span className="font-mono text-xs font-black block text-emerald-600">order_items</span>
                  <span className="text-[9px] text-slate-400">明细表</span>
                </div>
                <div className="bg-white border border-slate-200 rounded-lg p-2.5 w-[110px] text-center shadow">
                  <span className="font-mono text-xs font-black block text-slate-700">customers</span>
                  <span className="text-[9px] text-slate-400">客户表</span>
                </div>
                <div className="bg-white border border-purple-500 rounded-lg p-2.5 w-[110px] text-center shadow">
                  <span className="font-mono text-xs font-black block text-purple-600">payments</span>
                  <span className="text-[9px] text-slate-400">交易流水</span>
                </div>
              </div>

              {/* End mapping level leaf products */}
              <div className="flex flex-col justify-center">
                <div className="bg-white border border-emerald-500 rounded-lg p-2.5 w-[110px] text-center shadow">
                  <span className="font-mono text-xs font-black block text-emerald-600">products</span>
                  <span className="text-[9px] text-slate-400">商品属性</span>
                </div>
              </div>
            </div>
          </div>
          </CardContent>
        </Card>

      </div>

      {/* 3. Right Sidebar Security Policies Rule Cards */}
      <div className="w-[320px] bg-white border-l border-[#e2e8f0] h-screen flex flex-col shrink-0 select-none">
        <div className="p-4 border-b border-slate-100 text-left">
          <h3 className="text-sm font-bold text-slate-800">Agent 查询规则</h3>
          <p className="text-[10px] text-slate-400 font-semibold mt-0.5">控制大模型对所连接数据库的查询行为限制</p>
        </div>

        {/* Rule Scroller list */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {[
            {
              title: '只允许 SELECT 查询',
              desc: 'Agent 被硬编码安全权限约束。仅能组装 SELECT 汇总只读型指令，绝无写锁或更新。',
              badge: '通过'
            },
            {
              title: '默认 LIMIT 100 约束',
              desc: '所有查询默认强制拼装 LIMIT 100 逻辑拦截，最大保障数据库主节点吞吐，且防御长查询。',
              badge: '通过'
            },
            {
              title: '严苛禁止 DDL / DML 操作',
              desc: '通过内置 AST 词法树双重检测。拦截诸如 ALTER、DROP、DELETE、TRUNCATE 等恶意词汇行为。',
              badge: '通过'
            },
            {
              title: '敏感实体或字段不可查询',
              desc: '对含有买家身份标识、明文哈希等财务敏感实体实行字段断层脱敏，Agent 查询将被硬拒绝。',
              badge: '通过'
            },
            {
              title: '通过安全 SQL Safety Guard',
              desc: '利用多核检查机制对生成 SQL 进行解析评估，保障系统物理安全与风控合规。',
              badge: '通过'
            },
          ].map((rule, idx) => (
            <Card key={idx} className="border-slate-200 bg-slate-50 p-3.5 text-left font-sans shadow-none">
              <CardContent className="space-y-1.5 p-0">
              <div className="flex justify-between items-center">
                <h4 className="text-xs font-bold text-slate-800">{rule.title}</h4>
                <Badge className="bg-emerald-50 px-1.5 py-0.5 text-[9px] font-extrabold text-emerald-600">
                  {rule.badge}
                </Badge>
              </div>
              <p className="text-[10.5px] text-slate-400 font-semibold leading-relaxed">
                {rule.desc}
              </p>
              </CardContent>
            </Card>
          ))}
          
          <div className="p-3 bg-blue-50/50 rounded-xl border border-blue-100 text-left text-[11px] text-blue-700 font-bold leading-relaxed space-y-1">
            <span className="block font-black">口径说明</span>
            <span>规则由系统内核硬编码统一配置，如有进一步优化调优建议，请联系系统审计管理员。</span>
          </div>
        </div>
      </div>

    </div>
  );
}
