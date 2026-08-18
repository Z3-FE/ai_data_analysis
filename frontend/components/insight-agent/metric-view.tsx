"use client";

import React, { useEffect, useState } from 'react';
import { 
  Database, 
  Search, 
  ChevronDown, 
  Sliders, 
  Plus, 
  UploadCloud, 
  CheckCircle, 
  Clock, 
  FileText, 
  Eye, 
  Edit, 
  Trash2, 
  X,
  Play, 
  AlertCircle,
  Info
} from 'lucide-react';
import { apiGet } from '../../lib/api';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardFooter } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';

export default function MetricView() {
  /** 指标管理页面：加载指标资产，支持搜索/状态过滤，并展示右侧指标详情。 */

  const [metrics, setMetrics] = useState<any[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [selectedMetric, setSelectedMetric] = useState<any | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let ignore = false;

    async function loadMetrics() {
      /** 从后端读取指标语义资产，并默认选中第一条作为详情。 */

      try {
        setLoading(true);
        const data: any = await apiGet('/api/semantic', { asset_type: 'metrics' });
        if (ignore) return;
        const nextMetrics = data.metrics ?? [];
        setMetrics(nextMetrics);
        setSelectedMetric(nextMetrics[0] ?? null);
        setError('');
      } catch (err) {
        if (ignore) return;
        setError(err instanceof Error ? err.message : '指标接口请求失败');
      } finally {
        if (!ignore) setLoading(false);
      }
    }

    loadMetrics();

    return () => {
      ignore = true;
    };
  }, []);

  const filteredMetrics = metrics.filter(m => {
    const matchesSearch = m.name.toLowerCase().includes(searchQuery.toLowerCase()) || 
                          m.definition.toLowerCase().includes(searchQuery.toLowerCase()) ||
                          m.sql_expression.toLowerCase().includes(searchQuery.toLowerCase());
    
    if (statusFilter === 'all') return matchesSearch;
    return matchesSearch && m.status === statusFilter;
  });

  return (
    <div className="flex-1 flex overflow-hidden bg-slate-50 relative font-sans">
      
      {/* Main List Section */}
      <div className="flex-1 overflow-y-auto px-6 py-6 border-r border-[#e2e8f0]">
        
        {/* Header Title */}
        <div className="mb-6 text-left">
          <h2 className="text-xl font-extrabold text-slate-800 tracking-tight">指标管理</h2>
          <p className="text-xs text-slate-500 font-medium mt-1">
            管理销售额、订单数等企业核心指标，保障 Agent 按照统一的官方业务口径翻译自然语言并构建 SQL。
          </p>
        </div>

        {/* Top Highlight Stats Cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
          <Card className="border-slate-200/80 bg-white p-4 shadow-xs">
            <CardContent className="flex items-center gap-3 p-0">
            <div className="p-2.5 rounded-xl bg-emerald-50 text-emerald-600 shrink-0">
              <CheckCircle className="w-5 h-5" />
            </div>
            <div>
              <span className="block text-[10px] text-slate-400 font-bold uppercase tracking-wider">已发布指标</span>
              <span className="text-2xl font-black text-slate-800 font-mono tracking-tight">{metrics.filter(m => m.status === 'published').length}</span>
            </div>
            </CardContent>
          </Card>
          <Card className="border-slate-200/80 bg-white p-4 shadow-xs">
            <CardContent className="flex items-center gap-3 p-0">
            <div className="p-2.5 rounded-xl bg-amber-50 text-amber-600 shrink-0">
              <Clock className="w-5 h-5 animate-pulse" />
            </div>
            <div>
              <span className="block text-[10px] text-slate-400 font-bold uppercase tracking-wider">待审核指标</span>
              <span className="text-2xl font-black text-slate-800 font-mono tracking-tight">{metrics.filter(m => m.status === 'pending').length}</span>
            </div>
            </CardContent>
          </Card>
          <Card className="border-slate-200/80 bg-white p-4 shadow-xs">
            <CardContent className="flex items-center gap-3 p-0">
            <div className="p-2.5 rounded-xl bg-blue-50 text-blue-600 shrink-0">
              <UploadCloud className="w-5 h-5" />
            </div>
            <div>
              <span className="block text-[10px] text-slate-400 font-bold uppercase tracking-wider">文档导入候选</span>
              <span className="text-2xl font-black text-slate-800 font-mono tracking-tight">{metrics.filter(m => m.source?.includes('AI') || m.status === 'pending').length}</span>
            </div>
            </CardContent>
          </Card>
          <Card className="border-slate-200/80 bg-white p-4 shadow-xs">
            <CardContent className="flex items-center gap-3 p-0">
            <div className="p-2.5 rounded-xl bg-purple-50 text-purple-600 shrink-0">
              <Eye className="w-5 h-5" />
            </div>
            <div>
              <span className="block text-[10px] text-slate-400 font-bold uppercase tracking-wider">最近引用 (月)</span>
              <span className="text-2xl font-black text-slate-800 font-mono tracking-tight">{metrics.length}</span>
            </div>
            </CardContent>
          </Card>
        </div>

        {/* Selection / Filtering toolbar */}
        <Card className="mb-5 border-slate-200/80 bg-white p-4 shadow-xs">
          <CardContent className="flex flex-wrap items-center justify-between gap-4 p-0">
          <div className="flex flex-wrap items-center gap-3 flex-1 max-w-[500px]">
            {/* Search */}
            <div className="relative flex-1 min-w-[200px]">
              <Input
                type="text"
                placeholder="搜索指标物理名称或中文字段..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="h-9 rounded-lg border-slate-200 bg-slate-50 pl-9 text-xs font-medium focus-visible:ring-blue-500"
              />
              <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-3" />
            </div>

            {/* Filter */}
            <Select
                value={statusFilter}
                onValueChange={(value) => {
                  if (value) setStatusFilter(value);
                }}
              >
              <SelectTrigger className="h-9 rounded-lg border-slate-200 bg-slate-50 text-xs font-bold text-slate-600">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectItem value="all">状态：全部</SelectItem>
                  <SelectItem value="published">已发布</SelectItem>
                  <SelectItem value="pending">待审核</SelectItem>
                </SelectGroup>
              </SelectContent>
            </Select>
          </div>

          <div className="flex gap-2">
            <Button
              onClick={() => {
                alert('开启口径文档匹配检索算法...解析并导入 12 项潜在指标属性');
              }}
              variant="outline"
              className="h-9 rounded-lg border-blue-200 bg-blue-50 text-xs font-bold text-blue-600 hover:bg-blue-100/80 hover:text-blue-700"
            >
              <UploadCloud />
              <span>导入指标口径文档</span>
            </Button>
            <Button
              onClick={() => {
                alert('已成功创建新指标卡片模板，可在右侧指标详情栏完成字段解析。');
              }}
              className="h-9 rounded-lg bg-blue-600 text-xs font-bold text-white shadow-sm transition-all hover:bg-blue-700 active:scale-95"
            >
              <Plus />
              <span>新建指标</span>
            </Button>
          </div>
          </CardContent>
        </Card>

        {/* Metric Data Table */}
        {error && (
          <Alert className="mb-4 rounded-xl border-rose-100 bg-rose-50 text-xs font-bold text-rose-600">
            <AlertCircle className="size-4" />
            <AlertDescription>指标接口加载失败：{error}</AlertDescription>
          </Alert>
        )}
        {loading && (
          <Alert className="mb-4 rounded-xl border-blue-100 bg-blue-50 text-xs font-bold text-blue-600">
            <Info className="size-4" />
            <AlertDescription>正在从 FastAPI 加载指标资产...</AlertDescription>
          </Alert>
        )}
        <Card className="overflow-hidden rounded-xl border-slate-200/80 bg-white p-0 shadow-xs">
          <Table className="text-left text-xs text-slate-600">
              <TableHeader>
                <TableRow className="bg-slate-50 text-[10px] font-bold uppercase text-slate-400 hover:bg-slate-50">
                  <TableHead className="px-4 py-3">指标名称</TableHead>
                  <TableHead className="px-4 py-3">业务定义</TableHead>
                  <TableHead className="px-4 py-3">SQL 表达式</TableHead>
                  <TableHead className="px-4 py-3">可用维度</TableHead>
                  <TableHead className="px-4 py-3">发布来源</TableHead>
                  <TableHead className="px-4 py-3 text-center">状态</TableHead>
                  <TableHead className="px-4 py-3">更新时间</TableHead>
                  <TableHead className="px-4 py-3 text-center">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredMetrics.map((met) => {
                  const isSelect = selectedMetric?.id === met.id;
                  const isPub = met.status === 'published';
                  return (
                    <TableRow
                      key={met.id}
                      onClick={() => {
                        setSelectedMetric(met);
                        setDrawerOpen(true);
                      }}
                      className={`cursor-pointer border-slate-50 transition-colors hover:bg-blue-50/20 ${
                        isSelect ? 'bg-blue-50/50' : ''
                      }`}
                    >
                      <TableCell className="px-4 py-3">
                        <div className="flex flex-col text-left">
                          <span className="font-extrabold text-slate-800 mb-0.5">{met.name}</span>
                          <span className="font-mono text-[9px] text-slate-400">{met.metric_id}</span>
                        </div>
                      </TableCell>
                      <TableCell className="max-w-[200px] truncate px-4 py-3 font-medium text-slate-500" title={met.definition}>
                        {met.definition}
                      </TableCell>
                      <TableCell className="px-4 py-3">
                        <code className="bg-slate-50 border border-slate-200/60 rounded px-1.5 py-0.5 font-mono text-[10px] text-blue-600 font-semibold truncate max-w-[150px] inline-block">
                          {met.sql_expression}
                        </code>
                      </TableCell>
                      <TableCell className="px-4 py-3">
                        <div className="flex flex-wrap gap-1">
                          {(met.dimensions ?? []).map((d: string, index: number) => (
                            <Badge key={index} variant="secondary" className="h-5 rounded px-1.5 py-0.5 text-[9px] font-bold text-slate-600">
                              {d}
                            </Badge>
                          ))}
                        </div>
                      </TableCell>
                      <TableCell className="px-4 py-3 font-bold text-slate-400">{met.source}</TableCell>
                      <TableCell className="px-4 py-3 text-center">
                        <Badge variant="outline" className={`text-[9px] font-black ${
                          isPub 
                            ? 'bg-emerald-50 text-emerald-600 border border-emerald-100' 
                            : 'bg-amber-50 text-amber-600 border border-amber-100'
                        }`}>
                          {isPub ? '已发布' : '待审核'}
                        </Badge>
                      </TableCell>
                      <TableCell className="px-4 py-3 font-mono text-[10px] text-slate-400">{met.updated_at}</TableCell>
                      <TableCell className="px-4 py-3 text-center" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-center gap-1">
                          <Button
                            onClick={() => {
                              setSelectedMetric(met);
                              setDrawerOpen(true);
                            }}
                            variant="ghost"
                            size="icon-xs"
                            className="text-slate-400 hover:text-slate-700"
                            title="查物理细节"
                          >
                            <Eye />
                          </Button>
                          <Button
                            onClick={() => alert(`正在打开编辑窗口：修改 ${met.name} 口径设置`)}
                            variant="ghost"
                            size="icon-xs"
                            className="text-slate-400 hover:text-blue-600"
                            title="编辑"
                          >
                            <Edit />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          <CardFooter className="flex items-center justify-between border-t border-slate-50 bg-white p-3 text-[11px] font-semibold text-slate-400">
            <span>共 {filteredMetrics.length} 条记录</span>
            <div className="flex items-center gap-2">
              <span className="bg-blue-50 text-blue-600 px-2 py-1 rounded border border-blue-100">1</span>
              <span className="text-slate-500">10 条/页</span>
            </div>
          </CardFooter>
        </Card>

      </div>

      {/* Metric details side drawer */}
      {drawerOpen && selectedMetric && (
        <div className="w-[340px] bg-white h-screen border-l border-[#e2e8f0] flex flex-col shrink-0 select-none">
          {/* Header */}
          <div className="p-4 border-b border-slate-100 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Sliders className="w-4 h-4 text-blue-600" />
              <h3 className="text-sm font-bold text-slate-800">指标详情</h3>
            </div>
            <Button
              onClick={() => setDrawerOpen(false)}
              variant="ghost"
              className="h-8 w-8 rounded p-0 text-slate-400 hover:bg-slate-50 hover:text-slate-600"
            >
              <X className="w-4.5 h-4.5" />
            </Button>
          </div>

          {/* Drawer scroller */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs text-left">
            
            {/* Metric Title and ID */}
            <div>
              <div className="flex justify-between items-center mb-1">
                <span className="text-base font-black text-slate-800">{selectedMetric.name}</span>
                <Badge className={`px-2 py-0.5 text-[9px] font-black ${
                  selectedMetric.status === 'published' 
                    ? 'bg-emerald-50 text-emerald-600 border border-emerald-100' 
                    : 'bg-amber-50 text-amber-600 border border-amber-100'
                }`}>
                  {selectedMetric.status === 'published' ? '已发布' : '待审核'}
                </Badge>
              </div>
              <div className="bg-slate-50 border border-slate-200/60 font-mono text-[10px] text-slate-500 py-1.5 px-3 rounded-lg flex justify-between items-center">
                <span>指标 ID: </span>
                <span className="font-extrabold text-slate-800">{selectedMetric.metric_id}</span>
              </div>
            </div>

            {/* Business definition */}
            <div className="space-y-1">
              <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider">业务语义定义</span>
              <p className="bg-slate-50/50 rounded-lg p-2.5 border border-slate-100 text-slate-600 leading-relaxed font-semibold">
                {selectedMetric.definition}
              </p>
            </div>

            {/* SQL aggregations */}
            <div className="space-y-1">
              <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider">SQL 表示式公式</span>
              <pre className="bg-slate-900 text-blue-400 p-3 rounded-lg font-mono text-[10.5pt] border border-slate-850 overflow-x-auto leading-relaxed">
                {selectedMetric.sql_expression}
              </pre>
            </div>

            {/* Dependency entities */}
            <div className="space-y-1">
              <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider">依赖库表实体 (Dependencies)</span>
              <div className="flex flex-wrap gap-1.5 pt-1">
                {(selectedMetric.dependencies ?? []).map((dep: string, idx: number) => (
                  <Badge key={idx} variant="outline" className="border-slate-200/50 bg-slate-100 px-2.5 py-1 font-mono text-[10px] font-semibold text-slate-600">
                    {dep}
                  </Badge>
                ))}
              </div>
            </div>

            {/* Available dimensions */}
            <div className="space-y-1">
              <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider">关联可用维度</span>
              <div className="flex flex-wrap gap-1">
                {(selectedMetric.dimensions ?? []).map((dim: string, idx: number) => (
                  <Badge key={idx} className="border border-blue-100 bg-blue-50 px-2 py-0.5 text-[10px] font-extrabold text-blue-600">
                    {dim}
                  </Badge>
                ))}
              </div>
            </div>

            {/* Aliases */}
            <div className="space-y-1">
              <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider">业务中文同义词 (用于自然语言匹配)</span>
              <div className="flex flex-wrap gap-1">
                {(selectedMetric.synonyms ?? []).map((syn: string, idx: number) => (
                  <Badge key={idx} variant="outline" className="border-slate-200 bg-slate-50 px-2 py-0.5 text-[10px] font-semibold text-slate-500">
                    {syn}
                  </Badge>
                ))}
              </div>
            </div>

            {/* Sample Question */}
            <div className="space-y-1.5">
              <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider">推荐提问示例</span>
              <div className="p-2.5 bg-blue-50/50 text-blue-700 border border-blue-100 rounded-lg font-semibold flex items-start gap-1.5 leading-relaxed">
                <Info className="w-4 h-4 mt-0.5 shrink-0 text-blue-500" />
                <span>“{selectedMetric.sample_question}”</span>
              </div>
            </div>

            {/* Actions list */}
            <div className="border-t border-slate-100 pt-4 mt-6 flex flex-col gap-2">
              <Button 
                onClick={() => alert('已引用并跳转此指标运行统计页面')}
                variant="outline"
                className="w-full rounded-lg border-slate-200 bg-slate-50 px-3.5 py-1.5 text-xs font-bold text-slate-700 hover:bg-slate-100"
              >
                <Eye className="w-3.5 h-3.5" />
                <span>查看引用</span>
              </Button>
              <div className="flex gap-2">
                <Button 
                  onClick={() => alert(`正在编辑指标口径：${selectedMetric.name}`)}
                  className="flex-1 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-blue-700"
                >
                  <Edit className="w-3.5 h-3.5" />
                  <span>编辑指标</span>
                </Button>
                <Button 
                  onClick={() => alert('确认停用此统计指标口径？这可能会导致下游报表报错')}
                  variant="outline"
                  className="rounded-lg border-rose-200 bg-white px-3 py-1.5 text-xs font-bold text-rose-600 hover:bg-rose-50 hover:text-rose-700"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span>停用</span>
                </Button>
              </div>
            </div>

          </div>
        </div>
      )}

    </div>
  );
}
