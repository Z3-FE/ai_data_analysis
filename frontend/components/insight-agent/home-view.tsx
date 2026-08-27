"use client";

import React, { useState, useRef } from 'react';
import { useRouter } from 'next/navigation';
import {
  AlertCircle,
  Sparkles,
  Database,
  ChevronDown,
  Send,
  TrendingUp,
  Users,
  ClipboardList,
  Package,
  RefreshCw,
  DollarSign,
  CheckCircle2,
  Paperclip,
  UploadCloud,
  FileText,
  X,
  Loader2,
  Tags,
  Sliders
} from 'lucide-react';
import {
  mockGlossarySemanticDrafts,
  mockMetricSemanticDrafts,
} from '../../data/insight-agent';
import { apiPost } from '../../lib/api';
import { SemanticDraft, SemanticUploadCategory, SemanticUploadTask } from '../../types/insight-agent';
import SemanticUploadReview from './semantic-upload-review';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Input } from '@/components/ui/input';
import { Progress } from '@/components/ui/progress';
import { Textarea } from '@/components/ui/textarea';

interface HomeViewProps {
  onExecuteQuery?: (queryText: string) => void;
}

export default function HomeView({ onExecuteQuery }: HomeViewProps) {
  /** 新建会话首页：选择数据源、上传语义/指标文件、创建会话并跳转到分析页。 */

  const router = useRouter();
  const [queryInput, setQueryInput] = useState('');
  const [dbDropdownOpen, setDbDropdownOpen] = useState(false);
  const [selectedDb, setSelectedDb] = useState('Olist Ecommerce');
  const [selectedDbType, setSelectedDbType] = useState('PostgreSQL');

  // Custom upload states for general files, business glossaries, and metrics
  const [selectedCategory, setSelectedCategory] = useState<'file' | 'glossary' | 'metric'>('file');
  const [attachedFile, setAttachedFile] = useState<{ name: string; size: string; category: 'file' | 'glossary' | 'metric' } | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [isCreatingConversation, setIsCreatingConversation] = useState(false);
  const [createConversationError, setCreateConversationError] = useState('');
  const [semanticTask, setSemanticTask] = useState<SemanticUploadTask | null>(null);
  const [semanticDrafts, setSemanticDrafts] = useState<SemanticDraft[]>([]);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const adoptedSemanticDrafts = semanticDrafts.filter((draft) => draft.status === 'adopted');

  const formatFileSize = (file: File) => {
    /** 把浏览器 File 大小格式化为 KB/MB 文本。 */

    if (file.size > 1024 * 1024) {
      return (file.size / (1024 * 1024)).toFixed(1) + ' MB';
    }

    if (file.size > 1024) {
      return (file.size / 1024).toFixed(0) + ' KB';
    }

    return file.size + ' Bytes';
  };

  const buildSemanticDrafts = (category: SemanticUploadCategory, fileName: string) => {
    /** 根据上传文件类型生成待审核的模拟语义候选项。 */

    const sourceDrafts = category === 'metric' ? mockMetricSemanticDrafts : mockGlossarySemanticDrafts;

    return sourceDrafts.map((draft) => ({
      ...draft,
      status: 'pending' as const,
      sourceDocument: fileName,
      updatedAt: undefined,
      adoptedScope: undefined,
    }));
  };

  const handleTriggerUpload = (category: 'file' | 'glossary' | 'metric') => {
    /** 记录本次上传类型，并主动触发隐藏文件选择框。 */

    setSelectedCategory(category);
    if (fileInputRef.current) {
      fileInputRef.current.click();
    }
  };

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    /** 处理用户选择文件后的上传模拟、语义提取模拟和输入框回填。 */

    const file = e.target.files?.[0];
    if (!file) return;

    setIsUploading(true);
    setAttachedFile(null);
    setSemanticDrafts([]);

    const sizeStr = formatFileSize(file);
    const isSemanticUpload = selectedCategory === 'glossary' || selectedCategory === 'metric';

    if (isSemanticUpload) {
      const category = selectedCategory;
      const taskId = `semantic_task_${Date.now()}`;

      setSemanticTask({
        id: taskId,
        fileName: file.name,
        fileSize: sizeStr,
        category,
        status: 'uploading',
        progress: 28,
        message: '正在上传文档并做格式校验',
      });

      setTimeout(() => {
        setSemanticTask({
          id: taskId,
          fileName: file.name,
          fileSize: sizeStr,
          category,
          status: 'extracting',
          progress: 68,
          message: 'AI 正在提取候选指标、术语和规则',
        });
      }, 600);

      setTimeout(() => {
        setIsUploading(false);
        setAttachedFile({
          name: file.name,
          size: sizeStr,
          category,
        });
        setSemanticTask({
          id: taskId,
          fileName: file.name,
          fileSize: sizeStr,
          category,
          status: 'ready',
          progress: 100,
          message: '候选语义资产已生成，等待人工审核',
        });
        setSemanticDrafts(buildSemanticDrafts(category, file.name));
        setQueryInput((prev) => prev || '基于当前采用的语义资产，分析本月商品销售趋势');
      }, 1200);

      e.target.value = '';
      return;
    }

    setTimeout(() => {
      setIsUploading(false);

      setAttachedFile({
        name: file.name, 
        size: sizeStr,
        category: selectedCategory
      });

      setQueryInput(prev => prev || `结合 【${file.name}】 数据表提供一份全面的销售汇总趋势报告`);
    }, 1200);

    // Reset value to allow same-file-retriggering
    e.target.value = '';
  };

  const handleRemoveFile = () => {
    /** 移除已选择文件，并清空相关语义提取任务和候选项。 */

    setAttachedFile(null);
    setSemanticTask(null);
    setSemanticDrafts([]);
  };

  const handleUpdateDraft = (updatedDraft: SemanticDraft) => {
    /** 更新某一条语义候选项的审核编辑结果。 */

    setSemanticDrafts((prev) => prev.map((draft) => (draft.id === updatedDraft.id ? updatedDraft : draft)));
  };

  const handleAdoptDraft = (draftId: string) => {
    /** 将某一条语义候选项标记为本会话采用。 */

    setSemanticDrafts((prev) =>
      prev.map((draft) =>
        draft.id === draftId
          ? {
            ...draft,
            status: 'adopted',
            adoptedScope: 'session',
            updatedAt: '刚刚采用',
          }
          : draft,
      ),
    );
  };

  const handleRejectDraft = (draftId: string) => {
    /** 将某一条语义候选项标记为驳回。 */

    setSemanticDrafts((prev) =>
      prev.map((draft) =>
        draft.id === draftId
          ? {
            ...draft,
            status: 'rejected',
            updatedAt: '刚刚驳回',
          }
          : draft,
      ),
    );
  };

  const handleResetSemanticUpload = () => {
    /** 重置语义上传流程，回到普通提问状态。 */

    setSemanticTask(null);
    setSemanticDrafts([]);
    setAttachedFile(null);
  };

  const recommendedQuestions = [
    {
      title: '日常聊天',
      desc: '验证普通聊天路由和文字回答',
      query: '你好，请介绍一下你现在能做什么？',
      icon: TrendingUp,
      bgColor: 'bg-emerald-50',
      iconColor: 'text-emerald-500',
    },
    {
      title: '月度趋势问数',
      desc: '查询 2017 年每个月的销售额',
      query: '查询2017年每个月的销售额。',
      icon: Users,
      bgColor: 'bg-blue-50',
      iconColor: 'text-blue-500',
    },
    {
      title: '支付条件问数',
      desc: '查询指定支付方式下的销售额',
      query: '查询2017年信用卡支付的销售额。',
      icon: ClipboardList,
      bgColor: 'bg-purple-50',
      iconColor: 'text-purple-500',
    },
    {
      title: '品类统计问数',
      desc: '按商品品类统计年度销售额',
      query: '查询2017年按商品品类统计销售额。',
      icon: Package,
      bgColor: 'bg-amber-50',
      iconColor: 'text-amber-500',
    },
    {
      title: '地区统计问数',
      desc: '按卖家州统计年度销售额',
      query: '查询2017年按卖家州统计销售额。',
      icon: RefreshCw,
      bgColor: 'bg-cyan-50',
      iconColor: 'text-cyan-500',
    },
    {
      title: '下降归因分析',
      desc: '验证多任务分析和最终报告生成',
      query: '找出2017年销售额下降最明显的月份，并分析该月份下降最多的商品类别和卖家地区。',
      icon: DollarSign,
      bgColor: 'bg-pink-50',
      iconColor: 'text-pink-500',
    },
  ];

  const handleSend = async () => {
    /** 创建新会话，把首问暂存到 sessionStorage，并跳转到会话详情页自动发送。 */

    if (!queryInput.trim() && !attachedFile) return;
    let finalQueryText = queryInput;
    if (attachedFile && !queryInput.includes(attachedFile.name)) {
      const catText = attachedFile.category === 'file' ? '上传文件' : attachedFile.category === 'glossary' ? '业务术语库' : '指标口径定义';
      finalQueryText = `[已携带附件 - ${catText}: ${attachedFile.name}] ` + (queryInput.trim() || '分析汇总上传的关联数据');
    }
    if (onExecuteQuery) {
      onExecuteQuery(finalQueryText);
      return;
    }

    setIsCreatingConversation(true);
    setCreateConversationError('');
    try {
      const result = await apiPost('/api/conversations', {
        data_source_id: 'olist',
        adopted_semantic_draft_ids: adoptedSemanticDrafts.map((draft) => draft.id),
        adopted_semantic_draft_titles: adoptedSemanticDrafts.map((draft) => draft.title),
      });

      if (!result.conversation_id) {
        throw new Error('create conversation failed');
      }

      sessionStorage.setItem(`pending_question:${result.conversation_id}`, finalQueryText);
      window.dispatchEvent(new CustomEvent('insight:conversations:changed'));
      router.push(`/sessions/${result.conversation_id}`);
    } catch (error) {
      setCreateConversationError(
        error instanceof Error ? error.message : '创建会话失败，请确认后端服务已启动',
      );
    } finally {
      setIsCreatingConversation(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    /** 支持 Enter 发送问题，Shift+Enter 保留换行输入。 */

    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="flex-1 overflow-y-auto bg-[#fafbfc] px-6 py-10 font-sans">
      <div className="max-w-[940px] mx-auto flex flex-col items-center justify-center min-h-[80vh] py-4">

        {/* Animated Highlight Header Sparkle */}
        <div className="w-[72px] h-[72px] bg-blue-50 border border-blue-100 rounded-full flex items-center justify-center text-blue-600 mb-6 shadow-sm animate-pulse">
          <Sparkles className="w-8 h-8 fill-blue-500/10" />
        </div>

        {/* Central Greetings */}
        <h2 className="text-3xl font-extrabold text-slate-900 tracking-tight text-center mb-3">
          你好，今天想分析什么？
        </h2>
        <p className="text-sm text-slate-500 text-center mb-8 font-medium">
          基于 {selectedDb} 数据源，使用自然语言提问，获取数据分析与洞察
        </p>

        {/* DataSource Selector Area */}
        <div className="w-full max-w-[720px] mb-6">
          <label className="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-2 text-left">
            选择数据源
          </label>
          <div className="relative">
            <div
              onClick={() => setDbDropdownOpen(!dbDropdownOpen)}
              className="w-full bg-white hover:bg-slate-50/50 cursor-pointer py-3.5 px-4 rounded-xl border border-slate-200/90 shadow-sm flex items-center justify-between transition-all"
            >
              <div className="flex items-center gap-3">
                <div className="p-1 px-1.5 rounded-lg bg-emerald-50 text-emerald-600 border border-emerald-100/50">
                  <Database className="w-4 h-4" />
                </div>
                <div className="flex items-center gap-2">
                  <span className="text-sm font-bold text-slate-800">{selectedDb}</span>
                  <span className="text-xs text-slate-400 font-mono">{selectedDbType}</span>
                </div>
              </div>
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-1.5 text-xs text-emerald-600 font-bold bg-emerald-50 px-2.5 py-1 rounded-full border border-emerald-100">
                  <span className="w-1.5 h-1.5 bg-emerald-500 rounded-full animate-ping"></span>
                  <span>已启用</span>
                </div>
                <ChevronDown className={`w-4 h-4 text-slate-400 transition-transform duration-200 ${dbDropdownOpen ? 'rotate-180' : ''}`} />
              </div>
            </div>

            {/* DataSource Dropdown Options */}
            {dbDropdownOpen && (
              <div className="absolute top-full left-0 right-0 mt-1 bg-white border border-slate-200 rounded-xl shadow-xl z-20 py-1.5 overflow-hidden">
                <div
                  onClick={() => {
                    setSelectedDb('Olist Ecommerce');
                    setSelectedDbType('PostgreSQL');
                    setDbDropdownOpen(false);
                  }}
                  className="px-4 py-3 hover:bg-slate-50 flex items-center justify-between cursor-pointer transition-colors"
                >
                  <div className="flex items-center gap-3">
                    <Database className="w-4 h-4 text-slate-400" />
                    <div>
                      <div className="text-xs font-bold text-slate-800">Olist Ecommerce (默认)</div>
                      <div className="text-[10px] text-slate-400 font-mono">PostgreSQL 15.0</div>
                    </div>
                  </div>
                  <CheckCircle2 className="w-4 h-4 text-blue-600" />
                </div>
                <div
                  onClick={() => {
                    setSelectedDb('Northwind Warehouses');
                    setSelectedDbType('MySQL');
                    setDbDropdownOpen(false);
                  }}
                  className="px-4 py-3 hover:bg-slate-50 flex items-center justify-between cursor-pointer transition-colors border-t border-slate-100"
                >
                  <div className="flex items-center gap-3">
                    <Database className="w-4 h-4 text-slate-400" />
                    <div>
                      <div className="text-xs font-semibold text-slate-600">Northwind Warehouses</div>
                      <div className="text-[10px] text-slate-400 font-mono">MySQL 8.0</div>
                    </div>
                  </div>
                  <span className="text-[9px] bg-slate-100 text-slate-500 px-1 rounded">可连接</span>
                </div>
                <div
                  onClick={() => {
                    setSelectedDb('Shopify Analytics Data');
                    setSelectedDbType('ClickHouse');
                    setDbDropdownOpen(false);
                  }}
                  className="px-4 py-3 hover:bg-slate-50 flex items-center justify-between cursor-pointer transition-colors border-t border-slate-100"
                >
                  <div className="flex items-center gap-3">
                    <Database className="w-4 h-4 text-slate-400" />
                    <div>
                      <div className="text-xs font-semibold text-slate-600">Shopify Analytics Data</div>
                      <div className="text-[10px] text-slate-400 font-mono">ClickHouse OLAP</div>
                    </div>
                  </div>
                  <span className="text-[9px] bg-slate-100 text-slate-500 px-1 rounded">可连接</span>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Large Query TextArea Container */}
        <div className="w-full max-w-[720px] bg-white rounded-2xl border-2 border-blue-500 shadow-[0_4px_20px_rgba(59,130,246,0.08)] p-4 relative mb-12 focus-within:shadow-[0_4px_24px_rgba(59,130,246,0.15)] focus-within:border-blue-600 transition-all">
          {/* Hidden File Input */}
          <Input
            type="file"
            ref={fileInputRef}
            onChange={handleFileChange}
            className="hidden"
          />

          <Textarea
            value={queryInput}
            onChange={(e) => {
              setCreateConversationError('');
              setQueryInput(e.target.value.slice(0, 500));
            }}
            onKeyDown={handleKeyDown}
            placeholder="请输入你想分析的问题，例如：本月销售额最高的品类是什么？"
            className="mb-1 min-h-[125px] resize-none border-0 bg-transparent p-0 text-sm font-medium leading-relaxed text-slate-800 shadow-none placeholder:text-slate-400 focus-visible:ring-0"
          />

          {/* Sleek Attached File Chip / Badge */}
          {attachedFile && (
            <div className="mb-3 p-2.5 px-3.5 rounded-xl bg-slate-50 border border-slate-200 flex items-center justify-between text-xs animate-in fade-in slide-in-from-bottom-1 duration-200 select-none">
              <div className="flex items-center gap-3">
                <div className={`p-2 rounded-lg shrink-0 border ${attachedFile.category === 'file'
                    ? 'bg-blue-50 text-blue-600 border-blue-100'
                    : attachedFile.category === 'glossary'
                      ? 'bg-purple-50 text-purple-600 border-purple-100'
                      : 'bg-emerald-50 text-emerald-600 border-emerald-100'
                  }`}>
                  {attachedFile.category === 'file' && <FileText className="w-4 h-4" />}
                  {attachedFile.category === 'glossary' && <Tags className="w-4 h-4" />}
                  {attachedFile.category === 'metric' && <Sliders className="w-4 h-4" />}
                </div>
                <div className="text-left">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-extrabold text-slate-800 truncate max-w-[280px]">{attachedFile.name}</span>
                    <Badge className={`border border-slate-200 px-1.5 py-0.5 text-[9px] font-black ${attachedFile.category === 'file'
                        ? 'bg-blue-50 text-blue-600'
                        : attachedFile.category === 'glossary'
                          ? 'bg-purple-50 text-purple-600'
                          : 'bg-emerald-50 text-emerald-600'
                      }`}>
                      {attachedFile.category === 'file' && '普通文件'}
                      {attachedFile.category === 'glossary' && '业务术语'}
                      {attachedFile.category === 'metric' && '指标口径'}
                    </Badge>
                  </div>
                  <p className="text-[10px] text-slate-400 font-semibold font-mono mt-0.5">
                    大小: {attachedFile.size} • 状态: <span className="text-emerald-600 font-bold">
                      {attachedFile.category === 'file' ? '校验通过，可随问题一起分析' : '候选语义资产已生成，等待审核'}
                    </span>
                  </p>
                </div>
              </div>
              <Button
                type="button"
                onClick={handleRemoveFile}
                variant="ghost"
                className="h-8 w-8 rounded p-0 text-slate-400 hover:bg-slate-200/60 hover:text-slate-600"
                title="移除文件"
              >
                <X className="w-4 h-4" />
              </Button>
            </div>
          )}

          {/* Simulated uploading progress bar */}
          {isUploading && (
            <div className="mb-3 p-3 bg-slate-50 border border-slate-200 rounded-xl space-y-2 select-none">
              <div className="flex items-center justify-between text-xs font-bold text-slate-600">
                <div className="flex items-center gap-2">
                  <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-600" />
                  <span>智能解析与格式校对...</span>
                </div>
                <span className="font-mono text-[10px] text-blue-600">65%</span>
              </div>
              <Progress value={65} className="h-1.5 bg-slate-200" />
            </div>
          )}

          <div className="flex items-center justify-between border-t border-slate-100 pt-3 mt-1">
            <div className="flex items-center gap-3">
              {/* File Attachment Dropdown */}
              <DropdownMenu>
                <DropdownMenuTrigger className="flex items-center gap-1.5 rounded-lg border border-slate-200/90 px-3 py-1.5 text-xs font-bold text-slate-500 transition-all hover:border-blue-500 hover:bg-blue-50/50 hover:text-blue-600">
                  <Paperclip className="w-3.5 h-3.5" />
                  <span>上传数据/口径文件</span>
                  <ChevronDown className="w-3 h-3 text-slate-400" />
                </DropdownMenuTrigger>

                <DropdownMenuContent align="start" side="top" className="w-64 rounded-xl border-slate-200 bg-white p-0 text-left shadow-xl">
                  <div className="px-3.5 pt-1.5 pb-2 border-b border-slate-100">
                    <span className="text-[10px] uppercase font-bold tracking-wider text-slate-400 block">选择上传数据分类</span>
                  </div>

                  <DropdownMenuItem
                    onClick={() => handleTriggerUpload('file')}
                    className="flex cursor-pointer items-start gap-2.5 px-3.5 py-2 hover:bg-slate-50"
                  >
                    <div className="p-1.5 rounded-lg bg-blue-50 text-blue-600 group-hover:bg-blue-100 shrink-0">
                      <FileText className="w-4 h-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-xs font-bold text-slate-800">普通文件 (Excel/CSV/TXT)</div>
                      <div className="text-[10px] text-slate-400 font-medium truncate">上传本地临时主表及明细进行查询</div>
                    </div>
                  </DropdownMenuItem>

                  <DropdownMenuItem
                    onClick={() => handleTriggerUpload('glossary')}
                    className="flex cursor-pointer items-start gap-2.5 border-t border-slate-100 px-3.5 py-2 hover:bg-slate-50"
                  >
                    <div className="p-1.5 rounded-lg bg-purple-50 text-purple-600 group-hover:bg-purple-100 shrink-0">
                      <Tags className="w-4 h-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-xs font-bold text-slate-800">业务语义文档</div>
                      <div className="text-[10px] text-slate-400 font-medium truncate">解析术语、简称、业务规则和同义映射</div>
                    </div>
                  </DropdownMenuItem>

                  <DropdownMenuItem
                    onClick={() => handleTriggerUpload('metric')}
                    className="flex cursor-pointer items-start gap-2.5 border-t border-slate-100 px-3.5 py-2 hover:bg-slate-50"
                  >
                    <div className="p-1.5 rounded-lg bg-emerald-50 text-emerald-600 group-hover:bg-emerald-100 shrink-0">
                      <Sliders className="w-4 h-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="text-xs font-bold text-slate-800">指标口径文档</div>
                      <div className="text-[10px] text-slate-400 font-medium truncate">上传指标公式、维度规则和默认过滤</div>
                    </div>
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>

              <span className="text-[11px] text-slate-400 font-mono font-medium">
                {queryInput.length}/500
              </span>
            </div>

            <Button
              onClick={handleSend}
              disabled={(!queryInput.trim() && !attachedFile) || isCreatingConversation}
              className={`h-10 w-10 rounded-xl p-0 transition-all ${(queryInput.trim() || attachedFile) && !isCreatingConversation
                  ? 'bg-blue-600 text-white cursor-pointer hover:bg-blue-700 shadow-sm active:scale-95'
                  : 'bg-slate-100 text-slate-300 cursor-not-allowed'
                }`}
            >
              {isCreatingConversation ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
            </Button>
          </div>

          {createConversationError && (
            <Alert className="mt-3 border-rose-100 bg-rose-50 text-rose-600">
              <AlertCircle className="w-4 h-4 shrink-0" />
              <AlertDescription className="text-xs font-bold">{createConversationError}</AlertDescription>
            </Alert>
          )}
        </div>

        <SemanticUploadReview
          task={semanticTask}
          drafts={semanticDrafts}
          adoptedDrafts={adoptedSemanticDrafts}
          onUpdateDraft={handleUpdateDraft}
          onAdoptDraft={handleAdoptDraft}
          onRejectDraft={handleRejectDraft}
          onReset={handleResetSemanticUpload}
        />

        {/* Recommended Questions Section */}
        <div className="w-full max-w-[720px] text-left">
          <h3 className="text-sm font-bold text-slate-800 tracking-wide mb-4">
            推荐问题
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {recommendedQuestions.map((q, idx) => {
              const Icon = q.icon;
              return (
                <Card
                  key={idx}
                  onClick={() => {
                    setQueryInput(q.query);
                    // Focusing user directly by triggering execution or filling the input
                    // For outstanding UX, we autofill, but highlight the send button
                  }}
                  className="cursor-pointer border-slate-200/80 bg-white p-4 font-sans shadow-sm transition-all hover:border-slate-300 hover:bg-slate-50/50 hover:shadow-md"
                >
                  <CardContent className="flex items-start gap-3.5 p-0">
                  <div className={`p-2.5 rounded-xl ${q.bgColor} ${q.iconColor} shrink-0`}>
                    <Icon className="w-5 h-5" />
                  </div>
                  <div>
                    <h4 className="text-xs font-bold text-slate-800 mb-1">{q.title}</h4>
                    <p className="text-[11px] text-slate-400 font-medium leading-relaxed">{q.desc}</p>
                  </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </div>

      </div>
    </div>
  );
}
