"use client";

import React, { useState } from 'react';
import { 
  Search, 
  CheckSquare, 
  Plus, 
  Scan, 
  CheckCircle, 
  Clock, 
  Globe, 
  Eye, 
  Edit, 
  Trash2, 
  X,
  Info 
} from 'lucide-react';
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

export default function DimensionView() {
  /** 维度管理页面：展示维度资产，支持搜索/类型过滤。 */

  const [dimensions, setDimensions] = useState<any[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState('all');
  const [selectedDimension, setSelectedDimension] = useState<any | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const filteredDimensions = dimensions.filter(d => {
    const matchesSearch = d.name.toLowerCase().includes(searchQuery.toLowerCase()) || 
                          d.field_mapping.toLowerCase().includes(searchQuery.toLowerCase()) ||
                          d.dataset.toLowerCase().includes(searchQuery.toLowerCase());
    
    if (typeFilter === 'all') return matchesSearch;
    return matchesSearch && d.type === typeFilter;
  });

  return (
    <div className="flex-1 flex overflow-hidden bg-slate-50 relative font-sans font-medium">
      
      {/* Main List Column */}
      <div className="flex-1 overflow-y-auto px-6 py-6 border-r border-[#e2e8f0]">
        
        {/* Header Title */}
        <div className="mb-6 text-left font-sans">
          <h2 className="text-xl font-extrabold text-slate-800 tracking-tight">维度管理</h2>
          <p className="text-xs text-slate-500 font-semibold mt-1">
            定义用于分组、切片、汇总筛选的物理字段属性。通过建立维度映射，帮助 Agent 实现诸如“分州统计”、“按品类聚合”等复杂多维报表分析。
          </p>
        </div>

        {/* Highlight Stats Cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
          <Card className="border-slate-200/80 bg-white p-4 shadow-xs">
            <CardContent className="flex items-center gap-3 p-0">
              <div className="p-2.5 rounded-xl bg-emerald-50 text-emerald-600 shrink-0">
                <CheckCircle className="w-5 h-5" />
              </div>
              <div>
                <span className="block text-[10px] text-slate-400 font-bold uppercase tracking-wider">已发布维度</span>
                <span className="text-2xl font-black text-slate-800 font-mono tracking-tight">{dimensions.filter(d => d.status === 'published').length}</span>
              </div>
            </CardContent>
          </Card>
          <Card className="border-slate-200/80 bg-white p-4 shadow-xs">
            <CardContent className="flex items-center gap-3 p-0">
              <div className="p-2.5 rounded-xl bg-blue-50 text-blue-600 shrink-0">
                <Globe className="w-5 h-5 animate-pulse" />
              </div>
              <div>
                <span className="block text-[10px] text-slate-400 font-bold uppercase tracking-wider">地理维度</span>
                <span className="text-2xl font-black text-slate-800 font-mono tracking-tight">{dimensions.filter(d => d.type === '地理维度').length}</span>
              </div>
            </CardContent>
          </Card>
          <Card className="border-slate-200/80 bg-white p-4 shadow-xs">
            <CardContent className="flex items-center gap-3 p-0">
              <div className="p-2.5 rounded-xl bg-purple-50 text-purple-600 shrink-0">
                <Clock className="w-5 h-5" />
              </div>
              <div>
                <span className="block text-[10px] text-slate-400 font-bold uppercase tracking-wider">时间维度</span>
                <span className="text-2xl font-black text-slate-800 font-mono tracking-tight font-sans">{dimensions.filter(d => d.type === '时间维度').length}</span>
              </div>
            </CardContent>
          </Card>
          <Card className="border-slate-200/80 bg-white p-4 shadow-xs">
            <CardContent className="flex items-center gap-3 p-0">
              <div className="p-2.5 rounded-xl bg-amber-50 text-amber-600 shrink-0">
                <Clock className="w-5 h-5 animate-pulse" />
              </div>
              <div>
                <span className="block text-[10px] text-slate-400 font-bold uppercase tracking-wider">待确认维度</span>
                <span className="text-2xl font-black text-slate-800 font-mono tracking-tight font-sans">{dimensions.filter(d => d.status === 'pending').length}</span>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Toolbar panel */}
        <Card className="mb-5 border-slate-200/80 bg-white p-4 shadow-xs">
          <CardContent className="flex flex-wrap items-center justify-between gap-4 p-0">
            <div className="flex flex-wrap items-center gap-3 flex-1 max-w-[500px]">
            {/* Search */}
            <div className="relative flex-1 min-w-[200px]">
              <Input
                type="text"
                placeholder="搜索维度名称或物理对应字段..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="h-9 rounded-lg border-slate-200 bg-slate-50 pl-9 text-xs font-semibold focus-visible:ring-blue-500"
              />
              <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-3" />
            </div>

            {/* Filter */}
            <Select
                value={typeFilter}
                onValueChange={(value) => {
                  if (value) setTypeFilter(value);
                }}
              >
              <SelectTrigger className="h-9 rounded-lg border-slate-200 bg-slate-50 text-xs font-bold text-slate-600">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectGroup>
                  <SelectItem value="all">类型：全部</SelectItem>
                  <SelectItem value="分类维度">分类维度</SelectItem>
                  <SelectItem value="地理维度">地理维度</SelectItem>
                  <SelectItem value="时间维度">时间维度</SelectItem>
                  <SelectItem value="状态维度">状态维度</SelectItem>
                </SelectGroup>
              </SelectContent>
            </Select>
          </div>

          <div className="flex gap-2">
            <Button
              onClick={() => {
                alert('启动物理数据库索引扫描算法...已发现 48 个候选映射维度。');
              }}
              variant="outline"
              className="h-9 rounded-lg border-blue-200 bg-blue-50 px-3.5 text-xs font-bold text-blue-600 hover:bg-blue-100/80 hover:text-blue-700"
            >
              <Scan className="w-3.5 h-3.5 animate-pulse" />
              <span>扫描数据集映射</span>
            </Button>
            <Button
              onClick={() => {
                alert('已启动新建维度流程，请在右侧详情卡片或者弹出层中录入字段映射配置。');
              }}
              className="h-9 rounded-lg bg-blue-600 px-3.5 text-xs font-bold text-white shadow-sm hover:bg-blue-700 active:scale-95"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>新建维度</span>
            </Button>
          </div>
          </CardContent>
        </Card>

        {/* Dimension table layout */}
        {error && (
          <Alert className="mb-4 border-rose-100 bg-rose-50 text-rose-600">
            <AlertDescription className="text-xs font-bold">维度加载失败：{error}</AlertDescription>
          </Alert>
        )}
        {!loading && dimensions.length === 0 && (
          <Alert className="mb-4 border-blue-100 bg-blue-50 text-blue-600">
            <AlertDescription className="text-xs font-bold">暂无维度，请先创建维度定义</AlertDescription>
          </Alert>
        )}
        <Card className="overflow-hidden border-slate-200/80 bg-white p-0 shadow-xs">
          <div className="overflow-x-auto">
            <div className="w-[1200px]">
              <Table className="text-left text-xs text-slate-600">
              <TableHeader>
                <TableRow className="border-b border-slate-200 bg-slate-50 text-[10px] font-bold uppercase text-slate-400 hover:bg-slate-50">
                  <TableHead className="py-3 px-4 text-[10px] font-bold uppercase text-slate-400">维度名称</TableHead>
                  <TableHead className="py-3 px-4 text-[10px] font-bold uppercase text-slate-400">物理映射路径</TableHead>
                  <TableHead className="py-3 px-4 text-[10px] font-bold uppercase text-slate-400">所属数据集</TableHead>
                  <TableHead className="py-3 px-4 text-[10px] font-bold uppercase text-slate-400">维度类型</TableHead>
                  <TableHead className="py-3 px-4 text-[10px] font-bold uppercase text-slate-400">可用匹配指标</TableHead>
                  <TableHead className="py-3 px-4 text-[10px] font-bold uppercase text-slate-400">数据来源</TableHead>
                  <TableHead className="py-3 px-4 text-center text-[10px] font-bold uppercase text-slate-400">状态</TableHead>
                  <TableHead className="py-3 px-4 text-center text-[10px] font-bold uppercase text-slate-400">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredDimensions.map((dim) => {
                  const isSelect = selectedDimension?.id === dim.id;
                  const isPub = dim.status === 'published';
                  return (
                    <TableRow
                      key={dim.id}
                      onClick={() => {
                        setSelectedDimension(dim);
                        setDrawerOpen(true);
                      }}
                      className={`border-b border-slate-50 last:border-b-0 hover:bg-blue-50/20 cursor-pointer transition-colors ${
                        isSelect ? 'bg-blue-50/50' : ''
                      }`}
                    >
                      <TableCell className="py-3 px-4 text-left">
                        <span className="font-extrabold text-slate-800">{dim.name}</span>
                      </TableCell>
                      <TableCell className="py-3 px-4">
                        <code className="bg-slate-50 border border-slate-200/60 rounded px-1.5 py-0.5 font-mono text-[10px] text-blue-600 font-semibold truncate max-w-[200px] inline-block">
                          {dim.field_mapping}
                        </code>
                      </TableCell>
                      <TableCell className="py-3 px-4 font-mono font-bold text-slate-500">{dim.dataset}</TableCell>
                      <TableCell className="py-3 px-4">
                        <Badge className={`text-[10px] font-bold ${
                          dim.type === '地理维度' 
                            ? 'bg-blue-50 text-blue-600' 
                            : dim.type === '时间维度'
                              ? 'bg-purple-50 text-purple-600'
                              : 'bg-slate-100 text-slate-600'
                        }`}>
                          {dim.type}
                        </Badge>
                      </TableCell>
                      <TableCell className="py-3 px-4">
                        <div className="flex flex-wrap gap-1">
                          {(dim.available_metrics ?? []).map((met: string, idx: number) => (
                            <Badge key={idx} variant="outline" className="border-slate-100 bg-slate-50 px-1.5 py-0.5 text-[9px] font-bold text-slate-500">
                              {met}
                            </Badge>
                          ))}
                        </div>
                      </TableCell>
                      <TableCell className="py-3 px-4 font-bold text-slate-400">{dim.source}</TableCell>
                      <TableCell className="py-3 px-4 text-center">
                        <Badge className={`px-2 py-0.5 text-[9px] font-black ${
                          isPub 
                            ? 'bg-emerald-50 text-emerald-600 border border-emerald-100' 
                            : 'bg-amber-50 text-amber-600 border border-amber-100'
                        }`}>
                          {isPub ? '已发布' : '待确认'}
                        </Badge>
                      </TableCell>
                      <TableCell className="py-3 px-4 text-center" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-center gap-1">
                          <Button
                            onClick={() => {
                              setSelectedDimension(dim);
                              setDrawerOpen(true);
                            }}
                            variant="ghost"
                            className="h-7 w-7 rounded p-0 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                          >
                            <Eye className="w-3.5 h-3.5" />
                          </Button>
                          <Button
                            onClick={() => alert(`正在编辑维度口径：修改 ${dim.name} 字段关联及其元数据`)}
                            variant="ghost"
                            className="h-7 w-7 rounded p-0 text-slate-400 hover:bg-slate-100 hover:text-blue-600"
                          >
                            <Edit className="w-3.5 h-3.5" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
          </div>
          
          <CardFooter className="border-t border-slate-50 bg-white p-3 text-[11px] font-semibold text-slate-400">
            <div className="flex w-full items-center justify-between">
            <span>共 {filteredDimensions.length} 条记录</span>
            <div className="flex items-center gap-2">
              <span className="bg-blue-50 text-blue-600 px-2 py-1 rounded border border-blue-100">1</span>
              <span className="text-slate-500">10 条/页</span>
            </div>
            </div>
          </CardFooter>
      
        </Card>

      </div>

      {/* Details Side Drawer */}
      {drawerOpen && selectedDimension && (
        <div className="w-[340px] bg-white h-screen border-l border-[#e2e8f0] flex flex-col shrink-0 select-none">
          {/* Header */}
          <div className="p-4 border-b border-slate-100 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <CheckSquare className="w-4 h-4 text-blue-600" />
              <h3 className="text-sm font-bold text-slate-800">维度详情</h3>
            </div>
            <Button
              onClick={() => setDrawerOpen(false)}
              variant="ghost"
              className="h-8 w-8 rounded p-0 text-slate-400 hover:bg-slate-50 hover:text-slate-600"
            >
              <X className="w-4.5 h-4.5" />
            </Button>
          </div>

          {/* Scroller contents */}
          <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs text-left">
            <div>
              <div className="flex justify-between items-center mb-1">
                <span className="text-base font-black text-slate-800">{selectedDimension.name}</span>
                <Badge className={`px-2 py-0.5 text-[9px] font-black ${
                  selectedDimension.status === 'published' 
                    ? 'bg-emerald-50 text-emerald-600 border border-emerald-100' 
                    : 'bg-amber-50 text-amber-600 border border-amber-100'
                }`}>
                  {selectedDimension.status === 'published' ? '已发布' : '待确认'}
                </Badge>
              </div>
              <div className="bg-slate-50 border border-slate-200/60 font-mono text-[10px] text-slate-500 py-1.5 px-3 rounded-lg flex justify-between items-center">
                <span>所属数据表：</span>
                <span className="font-extrabold text-slate-800">{selectedDimension.dataset}</span>
              </div>
            </div>

            {/* Field Mapping */}
            <div className="space-y-1">
              <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider">物理关联字段</span>
              <pre className="bg-slate-900 text-blue-400 p-3 rounded-lg font-mono text-[10pt] border border-slate-800 overflow-x-auto leading-relaxed">
                {selectedDimension.field_mapping}
              </pre>
            </div>

            {/* Dimension Type */}
            <div className="space-y-1">
              <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider">维度大类归属</span>
              <div className="pt-1">
                <Badge className="border border-blue-100 bg-blue-50 px-2.5 py-1 text-[10px] font-extrabold text-blue-600">
                  {selectedDimension.type}
                </Badge>
              </div>
            </div>

            {/* Available Metrics */}
            <div className="space-y-1">
              <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider">支撑匹配的核心指标</span>
              <div className="flex flex-wrap gap-1.5 pt-1">
                {(selectedDimension.available_metrics ?? []).map((met: string, idx: number) => (
                  <Badge key={idx} variant="outline" className="border-slate-200/50 bg-slate-100 px-2.5 py-1 text-[10px] font-semibold text-slate-700">
                    {met}
                  </Badge>
                ))}
              </div>
            </div>

            {/* Synonyms */}
            <div className="space-y-1">
              <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider">自然语言匹配同义别名</span>
              <div className="flex flex-wrap gap-1">
                {(selectedDimension.synonyms ?? []).map((syn: string, idx: number) => (
                  <Badge key={idx} variant="outline" className="border-slate-200 bg-slate-50 px-2 py-0.5 text-[10.5px] font-semibold text-slate-500">
                    {syn}
                  </Badge>
                ))}
              </div>
            </div>

            {/* Default filters */}
            <div className="space-y-1">
              <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider">推荐清洗预过滤条件</span>
              <div className="pt-1">
                <Badge variant="outline" className="border-amber-100 bg-amber-50 px-2 py-0.5 text-[10px] font-bold text-amber-700">
                  {selectedDimension.common_filter || '不设硬过滤'}
                </Badge>
              </div>
            </div>

            {/* Interactive sample queries mock */}
            <div className="space-y-1.5">
              <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider">推荐提问示例</span>
              <div className="p-2.5 bg-blue-50/50 text-blue-700 border border-blue-100 rounded-lg flex items-start gap-1.5 font-semibold">
                <Info className="w-4 h-4 mt-0.5 shrink-0 text-blue-500" />
                <span>“{selectedDimension.sample_question}”</span>
              </div>
            </div>

            {/* Actions list drawer */}
            <div className="border-t border-slate-100 pt-4 mt-6 flex flex-col gap-2">
              <Button 
                onClick={() => alert(`关联检测：表 ${selectedDimension.dataset} 各指标维度可用连通性：100% `)}
                variant="outline"
                className="w-full rounded-lg border-slate-200 bg-slate-50 px-3.5 py-1.5 text-xs font-bold text-slate-700 hover:bg-slate-100"
              >
                <Eye className="w-3.5 h-3.5" />
                <span>关联探查</span>
              </Button>
              <div className="flex gap-2">
                <Button 
                  onClick={() => alert(`正在编辑维度关系映射：${selectedDimension.name}`)}
                  className="flex-1 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-blue-700"
                >
                  <Edit className="w-3.5 h-3.5" />
                  <span>修改维度</span>
                </Button>
                <Button 
                  onClick={() => alert('确定要暂时下线该关联切片维度么？这可能会限制 Agent 并行汇总的多维逻辑')}
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
