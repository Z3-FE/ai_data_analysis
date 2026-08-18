"use client";

import React, { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  Clock,
  Edit3,
  FileSearch,
  Globe2,
  Loader2,
  RotateCcw,
  Sparkles,
  X,
} from "lucide-react";
import { SemanticDraft, SemanticUploadTask } from "../../types/insight-agent";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardFooter, CardHeader } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";

interface SemanticUploadReviewProps {
  task: SemanticUploadTask | null;
  drafts: SemanticDraft[];
  adoptedDrafts: SemanticDraft[];
  onUpdateDraft: (draft: SemanticDraft) => void;
  onAdoptDraft: (draftId: string) => void;
  onRejectDraft: (draftId: string) => void;
  onReset: () => void;
}

function getKindLabel(kind: SemanticDraft["kind"]) {
  /** 把候选资产类型转换为审核卡片里的中文标签。 */

  switch (kind) {
    case "metric":
      return "指标候选";
    case "dimension":
      return "维度候选";
    case "glossary":
      return "业务术语";
    case "business_rule":
      return "业务规则";
    default:
      return "候选资产";
  }
}

function getStatusClass(status: SemanticDraft["status"]) {
  /** 根据候选资产审核状态返回对应的徽标样式。 */

  if (status === "adopted") {
    return "bg-emerald-50 text-emerald-600 border-emerald-100";
  }

  if (status === "rejected") {
    return "bg-rose-50 text-rose-600 border-rose-100";
  }

  return "bg-amber-50 text-amber-600 border-amber-100";
}

function getStatusText(status: SemanticDraft["status"]) {
  /** 把候选资产审核状态转换为中文展示文本。 */

  if (status === "adopted") return "已采用";
  if (status === "rejected") return "已驳回";
  return "待审核";
}

export default function SemanticUploadReview({
  task,
  drafts,
  adoptedDrafts,
  onUpdateDraft,
  onAdoptDraft,
  onRejectDraft,
  onReset,
}: SemanticUploadReviewProps) {
  /** 语义上传审核组件：展示 AI 提取候选项，并支持编辑、采用和驳回。 */

  const [selectedDraftId, setSelectedDraftId] = useState<string>("");
  const [editingDraftId, setEditingDraftId] = useState<string>("");
  const [editTitle, setEditTitle] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editMappingTarget, setEditMappingTarget] = useState("");

  useEffect(() => {
    if (!selectedDraftId && drafts.length > 0) {
      setSelectedDraftId(drafts[0].id);
    }
  }, [drafts, selectedDraftId]);

  const selectedDraft = useMemo(
    () => drafts.find((draft) => draft.id === selectedDraftId) ?? drafts[0],
    [drafts, selectedDraftId],
  );

  if (!task) {
    return null;
  }

  const pendingCount = drafts.filter((draft) => draft.status === "pending").length;
  const adoptedCount = adoptedDrafts.length;

  const startEdit = (draft: SemanticDraft) => {
    /** 进入编辑状态，并把当前候选项内容填入表单。 */

    setEditingDraftId(draft.id);
    setEditTitle(draft.title);
    setEditDescription(draft.description);
    setEditMappingTarget(draft.mappingTarget);
  };

  const saveEdit = () => {
    /** 保存候选项编辑结果，并回传给父组件更新状态。 */

    if (!selectedDraft) return;

    onUpdateDraft({
      ...selectedDraft,
      title: editTitle.trim() || selectedDraft.title,
      description: editDescription.trim() || selectedDraft.description,
      mappingTarget: editMappingTarget.trim() || selectedDraft.mappingTarget,
      updatedAt: "刚刚修改",
    });
    setEditingDraftId("");
  };

  return (
    <section className="w-full max-w-[980px] mb-10">
      <Card className="overflow-hidden rounded-3xl border-blue-100 bg-white p-0 shadow-[0_18px_60px_rgba(15,23,42,0.06)]">
        <CardHeader className="border-b border-slate-100 bg-gradient-to-r from-blue-50 via-white to-emerald-50 px-5 py-4">
          <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-2xl bg-blue-600 text-white flex items-center justify-center shadow-sm">
              {task.status === "ready" ? <CheckCircle2 className="w-5 h-5" /> : <Loader2 className="w-5 h-5 animate-spin" />}
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="text-sm font-black text-slate-900">AI 语义资产提取审核</h3>
                <Badge variant="outline" className="border-blue-100 bg-white px-2 py-0.5 text-[10px] font-black text-blue-600">
                  {task.category === "metric" ? "指标口径文档" : "业务语义文档"}
                </Badge>
              </div>
              <p className="text-[11px] text-slate-500 font-semibold mt-1">
                {task.fileName} · {task.fileSize} · {task.message}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-3">
            <div className="min-w-[160px]">
              <div className="flex items-center justify-between text-[10px] font-bold text-slate-400 mb-1">
                <span>提取进度</span>
                <span>{task.progress}%</span>
              </div>
              <Progress value={task.progress} className="h-2 bg-slate-100" />
            </div>
            <Button
              type="button"
              onClick={onReset}
              variant="outline"
              className="rounded-xl border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-500 hover:bg-slate-50 hover:text-slate-800"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              重新上传
            </Button>
          </div>
          </div>
        </CardHeader>

        <div className="grid grid-cols-1 xl:grid-cols-[1.05fr_0.95fr] gap-0">
          <div className="p-5 border-r border-slate-100">
            <div className="flex items-center justify-between mb-4">
              <div>
                <h4 className="text-xs font-black text-slate-800">候选语义资产</h4>
                <p className="text-[11px] text-slate-400 font-semibold mt-1">
                  待审核 {pendingCount} 条，已采用 {adoptedCount} 条。第一阶段默认采用到本次会话。
                </p>
              </div>
              <Badge className="gap-1.5 border border-blue-100 bg-blue-50 px-2 py-1 text-[10px] font-black text-blue-600">
                <Sparkles className="w-3 h-3" />
                AI 候选
              </Badge>
            </div>

            <div className="space-y-3">
              {drafts.map((draft) => {
                const isSelected = selectedDraft?.id === draft.id;

                return (
                  <Button
                    key={draft.id}
                    type="button"
                    onClick={() => setSelectedDraftId(draft.id)}
                    variant="ghost"
                    className={`h-auto w-full justify-start rounded-2xl border p-4 text-left transition-all ${
                      isSelected
                        ? "border-blue-200 bg-blue-50/60 shadow-sm"
                        : "border-slate-200 bg-white hover:border-blue-100 hover:bg-slate-50"
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2 mb-1">
                          <span className="text-sm font-black text-slate-900">{draft.title}</span>
                          <Badge className="bg-slate-100 px-2 py-0.5 text-[10px] font-black text-slate-500">
                            {getKindLabel(draft.kind)}
                          </Badge>
                          <Badge className={`border px-2 py-0.5 text-[10px] font-black ${getStatusClass(draft.status)}`}>
                            {getStatusText(draft.status)}
                          </Badge>
                        </div>
                        <p className="text-[11px] text-slate-500 leading-relaxed font-semibold line-clamp-2">
                          {draft.description}
                        </p>
                      </div>
                      <div className="text-right shrink-0">
                        <div className="text-[10px] text-slate-400 font-bold mb-1">置信度</div>
                        <div className="text-sm font-black text-blue-600">{Math.round(draft.confidence * 100)}%</div>
                      </div>
                    </div>
                  </Button>
                );
              })}
            </div>
          </div>

          <div className="p-5 bg-slate-50/60">
            {selectedDraft && (
              <div className="h-full flex flex-col gap-4">
                <div className="rounded-2xl border border-slate-200 bg-white p-4">
                  <div className="flex items-start justify-between gap-3 mb-4">
                    <div>
                      <div className="text-[10px] font-black text-slate-400 uppercase tracking-widest mb-1">候选详情</div>
                      {editingDraftId === selectedDraft.id ? (
                        <Input
                          value={editTitle}
                          onChange={(event) => setEditTitle(event.target.value)}
                          className="h-10 rounded-xl border-blue-200 bg-blue-50/40 text-sm font-black text-slate-900 focus-visible:ring-blue-500"
                        />
                      ) : (
                        <h4 className="text-lg font-black text-slate-900">{selectedDraft.title}</h4>
                      )}
                    </div>
                    <Badge className={`border px-2 py-1 text-[10px] font-black ${getStatusClass(selectedDraft.status)}`}>
                      {getStatusText(selectedDraft.status)}
                    </Badge>
                  </div>

                  <div className="space-y-3">
                    <div>
                      <label className="text-[10px] font-black text-slate-400 uppercase tracking-widest">业务解释</label>
                      {editingDraftId === selectedDraft.id ? (
                        <Textarea
                          value={editDescription}
                          onChange={(event) => setEditDescription(event.target.value)}
                          className="mt-1 min-h-[92px] rounded-xl border-blue-200 bg-blue-50/40 text-xs font-semibold text-slate-700 focus-visible:ring-blue-500"
                        />
                      ) : (
                        <p className="mt-1 text-xs font-semibold text-slate-600 leading-relaxed">{selectedDraft.description}</p>
                      )}
                    </div>

                    <div>
                      <label className="text-[10px] font-black text-slate-400 uppercase tracking-widest">映射目标</label>
                      {editingDraftId === selectedDraft.id ? (
                        <Input
                          value={editMappingTarget}
                          onChange={(event) => setEditMappingTarget(event.target.value)}
                          className="mt-1 h-10 rounded-xl border-blue-200 bg-blue-50/40 font-mono text-xs font-bold text-slate-700 focus-visible:ring-blue-500"
                        />
                      ) : (
                        <p className="mt-1 text-xs font-mono font-bold text-blue-600 bg-blue-50 rounded-lg px-2.5 py-2 border border-blue-100">
                          {selectedDraft.mappingTarget}
                        </p>
                      )}
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                      {selectedDraft.fields.map((field) => (
                        <div key={`${selectedDraft.id}-${field.label}`} className="rounded-xl border border-slate-100 bg-slate-50 px-3 py-2">
                          <div className="text-[10px] font-black text-slate-400 mb-1">{field.label}</div>
                          <div className="text-[11px] font-bold text-slate-700 leading-relaxed">{field.value}</div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                <div className="rounded-2xl border border-slate-200 bg-white p-4">
                  <div className="flex items-center gap-2 text-xs font-black text-slate-800 mb-2">
                    <FileSearch className="w-4 h-4 text-blue-600" />
                    来源片段
                  </div>
                  <p className="text-[11px] font-semibold text-slate-500 mb-2">
                    {selectedDraft.sourceDocument}
                  </p>
                  <blockquote className="rounded-xl bg-slate-50 border border-slate-100 px-3 py-2 text-xs font-semibold text-slate-600 leading-relaxed">
                    {selectedDraft.sourceSnippet}
                  </blockquote>
                </div>

                <div className="rounded-2xl border border-slate-200 bg-white p-4">
                  <div className="flex flex-wrap gap-2">
                    {editingDraftId === selectedDraft.id ? (
                      <>
                        <Button
                          type="button"
                          onClick={saveEdit}
                          className="rounded-xl bg-blue-600 px-3 py-2 text-xs font-black text-white hover:bg-blue-700"
                        >
                          <Check className="w-3.5 h-3.5" />
                          保存修改
                        </Button>
                        <Button
                          type="button"
                          onClick={() => setEditingDraftId("")}
                          variant="outline"
                          className="rounded-xl border-slate-200 px-3 py-2 text-xs font-black text-slate-500 hover:bg-slate-50"
                        >
                          取消
                        </Button>
                      </>
                    ) : (
                      <>
                        <Button
                          type="button"
                          onClick={() => onAdoptDraft(selectedDraft.id)}
                          disabled={selectedDraft.status === "adopted"}
                          className="rounded-xl bg-emerald-600 px-3 py-2 text-xs font-black text-white hover:bg-emerald-700 disabled:bg-slate-200 disabled:text-slate-400"
                        >
                          <CheckCircle2 className="w-3.5 h-3.5" />
                          采用到本次会话
                        </Button>
                        <Button
                          type="button"
                          onClick={() => startEdit(selectedDraft)}
                          variant="outline"
                          className="rounded-xl border-blue-200 bg-blue-50 px-3 py-2 text-xs font-black text-blue-600 hover:bg-blue-100 hover:text-blue-700"
                        >
                          <Edit3 className="w-3.5 h-3.5" />
                          修改后采用
                        </Button>
                        <Button
                          type="button"
                          onClick={() => onRejectDraft(selectedDraft.id)}
                          disabled={selectedDraft.status === "rejected"}
                          variant="outline"
                          className="rounded-xl border-rose-200 bg-rose-50 px-3 py-2 text-xs font-black text-rose-600 hover:bg-rose-100 hover:text-rose-700 disabled:border-slate-200 disabled:bg-slate-100 disabled:text-slate-400"
                        >
                          <X className="w-3.5 h-3.5" />
                          驳回
                        </Button>
                        <Button
                          type="button"
                          disabled
                          variant="outline"
                          className="rounded-xl border-slate-200 bg-slate-50 px-3 py-2 text-xs font-black text-slate-400"
                          title="Phase 2 只做占位，真实发布会进入后端权限审批"
                        >
                          <Globe2 className="w-3.5 h-3.5" />
                          发布到全局
                        </Button>
                      </>
                    )}
                  </div>

                  <Alert className="mt-3 border-amber-100 bg-amber-50 text-amber-700">
                    <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                    <AlertDescription className="text-[11px] font-semibold">当前阶段采用范围默认是“本次会话”，后续接后端时再支持全局发布和权限审批。</AlertDescription>
                  </Alert>
                </div>
              </div>
            )}
          </div>
        </div>

        <CardFooter className="border-t border-slate-100 bg-white px-5 py-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-black text-slate-800 flex items-center gap-1.5">
              <Clock className="w-3.5 h-3.5 text-slate-400" />
              当前会话已采用：
            </span>
            {adoptedDrafts.length === 0 ? (
              <span className="text-[11px] font-semibold text-slate-400">暂无，采用候选资产后会在这里形成会话级语义上下文。</span>
            ) : (
              adoptedDrafts.map((draft) => (
                <Badge
                  key={draft.id}
                  className="border border-emerald-100 bg-emerald-50 px-2.5 py-1 text-[11px] font-black text-emerald-700"
                >
                  {draft.title}
                </Badge>
              ))
            )}
          </div>
        </CardFooter>
      </Card>
    </section>
  );
}
