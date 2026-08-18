"use client";

import React, { useEffect, useState } from 'react';
import { 
  Tags, 
  Search, 
  Plus, 
  UploadCloud, 
  CheckCircle, 
  Clock, 
  Eye, 
  Edit, 
  Trash2, 
  X, 
  Info,
  Sliders,
  CheckSquare
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

export default function GlossaryView() {
  /** 业务术语页面：展示术语别名、业务规则和映射目标。 */

  const [glossaries, setGlossaries] = useState<any[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState('all');
  const [selectedGlossary, setSelectedGlossary] = useState<any | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let ignore = false;

    async function loadGlossary() {
      /** 从后端读取业务术语资产，并默认选中第一条作为详情。 */

      try {
        setLoading(true);
        const data: any = await apiGet('/api/semantic', { asset_type: 'glossary' });
        if (ignore) return;
        const nextGlossary = data.glossary ?? [];
        setGlossaries(nextGlossary);
        setSelectedGlossary(nextGlossary[0] ?? null);
        setError('');
      } catch (err) {
        if (ignore) return;
        setError(err instanceof Error ? err.message : '业务术语接口请求失败');
      } finally {
        if (!ignore) setLoading(false);
      }
    }

    loadGlossary();

    return () => {
      ignore = true;
    };
  }, []);

  const filteredGlossaries = glossaries.filter(g => {
    const matchesSearch = g.name.toLowerCase().includes(searchQuery.toLowerCase()) || 
                          g.mapping_target.toLowerCase().includes(searchQuery.toLowerCase()) ||
                          g.explanation.toLowerCase().includes(searchQuery.toLowerCase());
    
    if (typeFilter === 'all') return matchesSearch;
    return matchesSearch && g.type === typeFilter;
  });

  return (
    <div className="flex-1 flex overflow-hidden bg-slate-50 relative font-sans font-medium text-slate-800">
      
      {/* Main List panel */}
      <div className="flex-1 overflow-y-auto px-6 py-6 border-r border-[#e2e8f0]">
        
        {/* Header Title */}
        <div className="mb-6 text-left">
          <h2 className="text-xl font-extrabold text-slate-800 tracking-tight">业务术语</h2>
          <p className="text-xs text-slate-500 font-semibold mt-1">
            定义用户日常口语提问或简称，与底层物理模型/语义层指标间的同义对照路径。建立术语表，让 Agent 进行自然语言转换时具备更高的行业召回精准率。
          </p>
        </div>

        {/* Stats Summary cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
          <Card className="border-slate-200/80 bg-white p-4 shadow-xs">
            <CardContent className="flex items-center gap-3 p-0">
            <div className="p-2.5 rounded-xl bg-emerald-50 text-emerald-600 shrink-0">
              <CheckCircle className="w-5 h-5" />
            </div>
            <div>
              <span className="block text-[10px] text-slate-400 font-bold uppercase tracking-wider">已发布术语</span>
              <span className="text-2xl font-black text-slate-800 font-mono tracking-tight">{glossaries.filter(g => g.status === 'published').length} 条</span>
            </div>
            </CardContent>
          </Card>
          <Card className="border-slate-200/80 bg-white p-4 shadow-xs">
            <CardContent className="flex items-center gap-3 p-0">
            <div className="p-2.5 rounded-xl bg-blue-50 text-blue-600 shrink-0">
              <Sliders className="w-5 h-5 animate-pulse" />
            </div>
            <div>
              <span className="block text-[10px] text-slate-400 font-bold uppercase tracking-wider">指标别名</span>
              <span className="text-2xl font-black text-slate-800 font-mono tracking-tight font-sans">{glossaries.filter(g => g.type === '指标别名').length} 条</span>
            </div>
            </CardContent>
          </Card>
          <Card className="border-slate-200/80 bg-white p-4 shadow-xs">
            <CardContent className="flex items-center gap-3 p-0">
            <div className="p-2.5 rounded-xl bg-purple-50 text-purple-600 shrink-0">
              <CheckSquare className="w-5 h-5" />
            </div>
            <div>
              <span className="block text-[10px] text-slate-400 font-bold uppercase tracking-wider">维度别名</span>
              <span className="text-2xl font-black text-slate-800 font-mono tracking-tight font-sans">{glossaries.filter(g => g.type === '维度别名').length} 条</span>
            </div>
            </CardContent>
          </Card>
          <Card className="border-slate-200/80 bg-white p-4 shadow-xs">
            <CardContent className="flex items-center gap-3 p-0">
            <div className="p-2.5 rounded-xl bg-amber-50 text-amber-600 shrink-0">
              <Clock className="w-5 h-5 animate-pulse" />
            </div>
            <div>
              <span className="block text-[10px] text-slate-400 font-bold uppercase tracking-wider">待审核术语</span>
              <span className="text-2xl font-black text-slate-800 font-mono tracking-tight font-sans">{glossaries.filter(g => g.status === 'pending').length} 条</span>
            </div>
            </CardContent>
          </Card>
        </div>

        {/* Filters and search toolbar */}
        <Card className="mb-5 border-slate-200/80 bg-white p-4 shadow-xs">
          <CardContent className="flex flex-wrap items-center justify-between gap-4 p-0">
            <div className="flex flex-wrap items-center gap-3 flex-1 max-w-[500px]">
            {/* Search */}
            <div className="relative flex-1 min-w-[200px]">
              <Input
                type="text"
                placeholder="搜索术语英文简称、别名或映射目标..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="h-9 rounded-lg border-slate-200 bg-slate-50 pl-9 text-xs font-semibold focus-visible:ring-blue-500"
              />
              <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-3" />
            </div>

            {/* Filter type dropdown */}
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
                  <SelectItem value="指标别名">指标别名</SelectItem>
                  <SelectItem value="维度别名">维度别名</SelectItem>
                  <SelectItem value="分组规则">分组规则</SelectItem>
                  <SelectItem value="业务规则">业务规则</SelectItem>
                </SelectGroup>
              </SelectContent>
            </Select>
          </div>

          <div className="flex gap-2 font-sans">
            <Button
              onClick={() => {
                alert('启动业务词义挖掘机...已成功自 AI 模型推荐提取出 5 项待审业务近义词');
              }}
              variant="outline"
              className="h-9 rounded-lg border-blue-200 bg-blue-50 px-3.5 text-xs font-bold text-blue-600 hover:bg-blue-100/80 hover:text-blue-700"
            >
              <UploadCloud className="w-3.5 h-3.5" />
              <span>导入口径白皮书</span>
            </Button>
            <Button
              onClick={() => {
                alert('已启动术语创建模板。请在右侧进行映射目标指定。');
              }}
              className="h-9 rounded-lg bg-blue-600 px-3.5 text-xs font-bold text-white shadow-sm hover:bg-blue-700 active:scale-95"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>新建术语</span>
            </Button>
          </div>
          </CardContent>
        </Card>

        {/* Glossary Table list */}
        {error && (
          <Alert className="mb-4 border-rose-100 bg-rose-50 text-rose-600">
            <AlertDescription className="text-xs font-bold">业务术语接口加载失败：{error}</AlertDescription>
          </Alert>
        )}
        {loading && (
          <Alert className="mb-4 border-blue-100 bg-blue-50 text-blue-600">
            <AlertDescription className="text-xs font-bold">正在从 FastAPI 加载业务术语资产...</AlertDescription>
          </Alert>
        )}
        <Card className="overflow-hidden border-slate-200/80 bg-white p-0 shadow-xs">
          <div className="overflow-x-auto text-left">
            <Table className="text-xs text-slate-600">
              <TableHeader>
                <TableRow className="border-b border-slate-200 bg-slate-50 text-[10px] font-bold uppercase text-slate-400 hover:bg-slate-50">
                  <TableHead className="px-4 py-3 text-[10px] font-bold uppercase text-slate-400">术语名称</TableHead>
                  <TableHead className="px-4 py-3 text-[10px] font-bold uppercase text-slate-400">作用类型</TableHead>
                  <TableHead className="px-4 py-3 text-[10px] font-bold uppercase text-slate-400">映射目标</TableHead>
                  <TableHead className="px-4 py-3 text-[10px] font-bold uppercase text-slate-400">规则语义说明</TableHead>
                  <TableHead className="px-4 py-3 text-[10px] font-bold uppercase text-slate-400">发布来源</TableHead>
                  <TableHead className="px-4 py-3 text-center text-[10px] font-bold uppercase text-slate-400">冲突校验</TableHead>
                  <TableHead className="px-4 py-3 text-center text-[10px] font-bold uppercase text-slate-400">状态</TableHead>
                  <TableHead className="px-4 py-3 text-[10px] font-bold uppercase text-slate-400">最近被调用</TableHead>
                  <TableHead className="px-4 py-3 text-center text-[10px] font-bold uppercase text-slate-400">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredGlossaries.map((glo) => {
                  const isSelect = selectedGlossary?.id === glo.id;
                  const isPub = glo.status === 'published';
                  return (
                    <TableRow
                      key={glo.id}
                      onClick={() => {
                        setSelectedGlossary(glo);
                        setDrawerOpen(true);
                      }}
                      className={`border-b border-slate-50 last:border-b-0 hover:bg-blue-50/20 cursor-pointer transition-colors ${
                        isSelect ? 'bg-blue-50/50' : ''
                      }`}
                    >
                      <TableCell className="px-4 py-3 text-left">
                        <span className="font-extrabold text-slate-800 font-sans">{glo.name}</span>
                      </TableCell>
                      <TableCell className="px-4 py-3">
                        <Badge className={`px-2 py-0.5 text-[9px] font-black ${
                          glo.type === '指标别名' 
                            ? 'bg-blue-50 text-blue-600 text-[95%]' 
                            : glo.type === '维度别名'
                              ? 'bg-purple-50 text-purple-600'
                              : 'bg-amber-50 text-amber-600'
                        }`}>
                          {glo.type}
                        </Badge>
                      </TableCell>
                      <TableCell className="px-4 py-3 font-sans font-bold text-slate-700">{glo.mapping_target}</TableCell>
                      <TableCell className="max-w-[200px] truncate px-4 py-3 font-semibold text-slate-500" title={glo.explanation}>
                        {glo.explanation}
                      </TableCell>
                      <TableCell className="px-4 py-3 font-bold text-slate-400">{glo.source}</TableCell>
                      <TableCell className="px-4 py-3 text-center">
                        <Badge className="bg-emerald-50 px-1.5 py-0.5 text-[9px] font-black text-emerald-600">
                          {glo.conflict_check || '无冲突'}
                        </Badge>
                      </TableCell>
                      <TableCell className="px-4 py-3 text-center">
                        <Badge className={`px-2 py-0.5 text-[9px] font-black ${
                          isPub 
                            ? 'bg-emerald-50 text-emerald-600 border border-emerald-100' 
                            : 'bg-amber-50 text-amber-600 border border-amber-100'
                        }`}>
                          {isPub ? '已发布' : '待审核'}
                        </Badge>
                      </TableCell>
                      <TableCell className="px-4 py-3 font-mono text-[10px] text-slate-400">{glo.last_used}</TableCell>
                      <TableCell className="px-4 py-3 text-center" onClick={(e) => e.stopPropagation()}>
                        <div className="flex items-center justify-center gap-1">
                          <Button
                            onClick={() => {
                              setSelectedGlossary(glo);
                              setDrawerOpen(true);
                            }}
                            variant="ghost"
                            className="h-7 w-7 rounded p-0 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                          >
                            <Eye className="w-3.5 h-3.5" />
                          </Button>
                          <Button
                            onClick={() => alert(`正在编辑业务别名映射：${glo.name}`)}
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
          <CardFooter className="border-t border-slate-50 bg-white p-3 text-[11px] font-semibold text-slate-400">
            <div className="flex w-full items-center justify-between">
            <span>共 {filteredGlossaries.length} 条记录</span>
            <div className="flex items-center gap-2">
              <span className="bg-blue-50 text-blue-600 px-2 py-1 rounded border border-blue-100">1</span>
              <span className="text-slate-500">10 条/页</span>
            </div>
            </div>
          </CardFooter>
        </Card>

      </div>

      {/* Details Side Drawer Panel */}
      {drawerOpen && selectedGlossary && (
        <div className="w-[340px] bg-white h-screen border-l border-[#e2e8f0] flex flex-col shrink-0 select-none">
          {/* Header */}
          <div className="p-4 border-b border-slate-100 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Tags className="w-4 h-4 text-blue-600" />
              <h3 className="text-sm font-bold text-slate-800">术语映射详情</h3>
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
          <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs text-left font-sans font-semibold">
            
            {/* Term Title */}
            <div>
              <div className="flex justify-between items-center mb-1">
                <span className="text-base font-black text-slate-800 font-sans">{selectedGlossary.name}</span>
                <Badge className={`px-2 py-0.5 text-[9px] font-black ${
                  selectedGlossary.status === 'published' 
                    ? 'bg-emerald-50 text-emerald-600 border border-emerald-100' 
                    : 'bg-amber-50 text-amber-600 border border-amber-100'
                }`}>
                  {selectedGlossary.status === 'published' ? '已发布' : '待审核'}
                </Badge>
              </div>
              <div className="bg-slate-50 border border-slate-200/60 font-mono text-[10px] text-slate-500 py-1.5 px-3 rounded-lg flex justify-between items-center">
                <span>术语大类：</span>
                <span className="font-extrabold text-blue-600">{selectedGlossary.type}</span>
              </div>
            </div>

            {/* Mapping Target */}
            <div className="space-y-1">
              <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider">业务口径映射目标</span>
              <pre className="bg-slate-900 text-blue-400 p-3 rounded-lg font-mono text-[9.5pt] border border-slate-800 overflow-x-auto leading-relaxed">
                {selectedGlossary.mapping_target}
              </pre>
            </div>

            {/* Explanation business context */}
            <div className="space-y-1">
              <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider">别名映射及规则解释</span>
              <p className="bg-slate-50/50 rounded-lg p-2.5 border border-slate-100 text-slate-500 leading-relaxed font-bold">
                {selectedGlossary.explanation}
              </p>
            </div>

            {/* Agent parsing mock visual demonstration */}
            {selectedGlossary.agent_result && (
              <div className="space-y-1">
                <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider">Agent 词义转换分析结果示范</span>
                <div className="bg-blue-50/60 border border-blue-150 p-3 rounded-lg text-[10.5px] font-bold font-mono text-blue-800 leading-relaxed">
                  {selectedGlossary.agent_result}
                </div>
              </div>
            )}

            {/* Synonyms */}
            <div className="space-y-1">
              <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider">常用同近义简称字典</span>
              <div className="flex flex-wrap gap-1 pt-1">
                {['成交总额', '成交金额', '销售额累计', 'Gross Merchandise Volume'].map((syn, idx) => (
                  <Badge key={idx} variant="outline" className="border-slate-200/50 bg-slate-100 px-2.5 py-1 text-[10px] font-bold text-slate-500">
                    {syn}
                  </Badge>
                ))}
              </div>
            </div>

            {/* Source */}
            <div className="space-y-1 text-slate-400">
              <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider">录入来源</span>
              <span className="block text-[11px] font-bold mt-1 text-slate-600">{selectedGlossary.source}</span>
            </div>

            {/* Interactive sample questions */}
            {selectedGlossary.sample_question && (
              <div className="space-y-1.5">
                <span className="block text-[10px] font-bold text-slate-400 uppercase tracking-wider">推荐提问示例</span>
                <div className="p-2.5 bg-blue-50/50 text-blue-700 border border-blue-100 rounded-lg flex items-start gap-1.5 leading-relaxed">
                  <Info className="w-4 h-4 mt-0.5 shrink-0 text-blue-500" />
                  <span>“{selectedGlossary.sample_question}”</span>
                </div>
              </div>
            )}

            {/* Action buttons list */}
            <div className="border-t border-slate-100 pt-4 mt-6 flex flex-col gap-2 font-sans">
              <Button 
                onClick={() => alert(`已为该词建立关联：指标 ${selectedGlossary.mapping_target} 可召回率：99.4%`)}
                variant="outline"
                className="w-full rounded-lg border-slate-200 bg-slate-50 px-3.5 py-1.5 text-xs font-bold text-slate-700 hover:bg-slate-100"
              >
                <Eye className="w-3.5 h-3.5" />
                <span>查看关联口径</span>
              </Button>
              <div className="flex gap-2">
                <Button 
                  onClick={() => alert(`正在编辑业务口径术语：${selectedGlossary.name}`)}
                  className="flex-1 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-blue-700"
                >
                  <Edit className="w-3.5 h-3.5" />
                  <span>编辑术语</span>
                </Button>
                <Button 
                  onClick={() => alert('确认暂停或禁用此词条在语义大语言模型转换中的映射机制？')}
                  variant="outline"
                  className="rounded-lg border-rose-200 bg-white px-3 py-1.5 text-xs font-bold text-rose-600 hover:bg-rose-50 hover:text-rose-700"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span>禁用别名</span>
                </Button>
              </div>
            </div>

          </div>
        </div>
      )}

    </div>
  );
}
