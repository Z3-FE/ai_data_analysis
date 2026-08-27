"use client";

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  BookOpen,
  CheckSquare,
  ChevronDown,
  ChevronRight,
  Database,
  FileText,
  MessageSquare,
  Plus,
  Search,
  Sliders,
  Tags,
  Trash2,
  User,
} from "lucide-react";
import { apiDelete, apiGet, invalidateApiCache } from "../../lib/api";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";

interface SidebarConversation {
  conversation_id: string;
  title: string;
  updated_at?: string;
  created_at?: string;
  last_message_preview?: string | null;
}

function formatConversationTime(value?: string) {
  /** 把后端更新时间格式化为侧边栏展示用的时间文本。 */

  if (!value) return "--";

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";

  const now = new Date();
  const isToday = date.toDateString() === now.toDateString();

  if (isToday) {
    return date.toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  return date.toLocaleDateString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
  });
}

export default function Sidebar() {
  /** 左侧导航栏：展示新建会话、历史会话、数据源和语义资产入口。 */

  const pathname = usePathname();
  const router = useRouter();
  const [semanticExpanded, setSemanticExpanded] = useState(true);
  const [historyExpanded, setHistoryExpanded] = useState(true);
  const [searchHistory, setSearchHistory] = useState("");
  const [conversations, setConversations] = useState<SidebarConversation[]>([]);
  const [isLoadingConversations, setIsLoadingConversations] = useState(true);
  const [conversationError, setConversationError] = useState("");
  const [deletingConversationId, setDeletingConversationId] = useState<string | null>(null);

  const activeConversationId = pathname.startsWith("/sessions/")
    ? decodeURIComponent(pathname.split("/").filter(Boolean).at(-1) ?? "")
    : "";

  const loadConversations = useCallback(async () => {
    /** 从后端拉取真实会话列表，供历史记录区域展示。 */

    try {
      setIsLoadingConversations(true);
      const data = await apiGet("/api/conversations");
      setConversations(data.conversations ?? []);
      setConversationError("");
    } catch (error) {
      setConversationError(error instanceof Error ? error.message : "会话列表加载失败");
    } finally {
      setIsLoadingConversations(false);
    }
  }, []);

  useEffect(() => {
    void loadConversations();
  }, [loadConversations, pathname]);

  useEffect(() => {
    const handleConversationChanged = () => {
      invalidateApiCache("/api/conversations");
      void loadConversations();
    };

    window.addEventListener("insight:conversations:changed", handleConversationChanged);
    return () => {
      window.removeEventListener("insight:conversations:changed", handleConversationChanged);
    };
  }, [loadConversations]);

  const filteredConversations = conversations.filter((conversation) =>
    conversation.title.toLowerCase().includes(searchHistory.toLowerCase()),
  );

  const handleDeleteConversation = async (
    event: React.MouseEvent<HTMLButtonElement>,
    conversation: SidebarConversation,
  ) => {
    event.stopPropagation();
    if (deletingConversationId) return;

    const confirmed = window.confirm("确定删除会话“" + conversation.title + "”吗？删除后无法恢复。");
    if (!confirmed) return;

    try {
      setDeletingConversationId(conversation.conversation_id);
      await apiDelete("/api/conversations", {
        conversation_id: conversation.conversation_id,
      });
      setConversations((current) =>
        current.filter((item) => item.conversation_id !== conversation.conversation_id),
      );

      if (activeConversationId === conversation.conversation_id) {
        router.push("/");
      }
    } catch (error) {
      setConversationError(error instanceof Error ? error.message : "会话删除失败");
    } finally {
      setDeletingConversationId(null);
    }
  };

  const navItemClass = (isActive: boolean) =>
    /** 根据是否激活生成一级导航项样式。 */

    cn(
      "w-full flex items-center gap-2 py-2.5 px-3 rounded-lg font-medium transition-colors",
      isActive
        ? "bg-blue-50 text-blue-600 border border-blue-100/50"
        : "text-slate-600 hover:bg-slate-200/50 hover:text-slate-800",
    );

  const semanticItemClass = (isActive: boolean) =>
    /** 根据是否激活生成语义资产二级菜单样式。 */

    cn(
      "w-full text-left py-2 px-3 rounded-lg flex items-center gap-2 transition-colors text-xs font-semibold",
      isActive ? "bg-blue-50 text-blue-600" : "text-slate-600 hover:bg-slate-200/50 hover:text-slate-800",
    );

  return (
    <div className="w-[260px] border-r border-[#e2e8f0] bg-slate-50 min-h-screen flex flex-col justify-between shrink-0 select-none font-sans">
      <div className="flex flex-col flex-1 overflow-y-auto">
        <Link
          href="/"
          className="p-5 flex items-center gap-3 border-b border-[#e2e8f0] bg-white cursor-pointer hover:bg-slate-50/50 transition-colors"
        >
          <div className="w-10 h-10 rounded-xl bg-blue-600 flex items-center justify-center text-white font-bold text-lg shadow-sm">
            AI
          </div>
          <div className="flex flex-col">
            <h1 className="text-sm font-semibold text-slate-800 tracking-tight flex items-center gap-1.5 font-sans">
              Insight Agent
            </h1>
            <span className="text-[10px] text-slate-500 font-normal">企业级数据智能分析与决策平台</span>
          </div>
        </Link>

        <div className="p-4">
          <Link
            href="/"
            id="new-session-btn"
            className={cn(
              buttonVariants(),
              "w-full rounded-xl bg-blue-600 text-sm font-medium text-white shadow-sm transition-all hover:bg-blue-700 hover:shadow-md active:scale-95",
            )}
          >
            <Plus />
            <span>新建会话</span>
          </Link>
        </div>

        <nav className="flex-1 px-3 space-y-1 text-sm">
          <div>
            <Button
              type="button"
              onClick={() => setHistoryExpanded(!historyExpanded)}
              variant="ghost"
              className="h-auto w-full justify-between px-2 py-2 font-medium text-slate-500 hover:bg-transparent hover:text-slate-800"
            >
              <div className="flex items-center gap-2">
                <MessageSquare className="w-4 h-4" />
                <span>会话记录</span>
              </div>
              {historyExpanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
            </Button>

            {historyExpanded && (
              <div className="mt-1 space-y-0.5 pl-2 transition-all duration-200">
                <div className="px-2 pb-1.5">
                  <div className="relative">
                    <Input
                      type="text"
                      placeholder="搜索会话..."
                      value={searchHistory}
                      onChange={(event) => setSearchHistory(event.target.value)}
                      className="h-8 rounded-lg border-slate-200 bg-white pl-7 text-xs font-semibold text-slate-700 focus-visible:ring-blue-500"
                    />
                    <Search className="w-3 h-3 text-slate-400 absolute left-2.5 top-2.5" />
                  </div>
                </div>

                <div className="max-h-[210px] space-y-0.5 overflow-y-auto pr-1">
                  {isLoadingConversations && (
                    <div className="p-2 text-center text-[11px] font-semibold text-slate-400">加载会话中...</div>
                  )}

                  {conversationError && (
                    <div className="p-2 text-center text-[11px] font-semibold text-rose-500">{conversationError}</div>
                  )}

                  {!isLoadingConversations && !conversationError && filteredConversations.map((conversation) => {
                    const isActive = activeConversationId === conversation.conversation_id;
                    const timeText = formatConversationTime(conversation.updated_at ?? conversation.created_at);

                    return (
                      <div
                        key={conversation.conversation_id}
                        role="button"
                        tabIndex={0}
                        onClick={() => router.push("/sessions/" + conversation.conversation_id)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            router.push("/sessions/" + conversation.conversation_id);
                          }
                        }}
                        className={cn(
                          "w-full cursor-pointer rounded-lg flex items-center gap-1 group transition-all text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500/40",
                          isActive
                            ? "bg-blue-50 text-blue-600 border border-blue-100"
                            : "text-slate-600 hover:bg-slate-200/50 hover:text-slate-800",
                        )}
                      >
                        <div className="min-w-0 flex-1 py-2 px-2.5" title={conversation.title}>
                          <span className="block truncate font-sans font-medium">{conversation.title}</span>
                        </div>
                        <span className="shrink-0 text-[10px] text-slate-400">{timeText}</span>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-xs"
                          onClick={(event) => void handleDeleteConversation(event, conversation)}
                          disabled={deletingConversationId !== null}
                          aria-label={"删除会话：" + conversation.title}
                          title="删除会话"
                          className="mr-1 text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                        >
                          <Trash2 />
                        </Button>
                      </div>
                    );
                  })}

                  {!isLoadingConversations && !conversationError && filteredConversations.length === 0 && (
                    <div className="text-[11px] text-slate-400 p-2 text-center">暂无符合条件的历史</div>
                  )}
                </div>
              </div>
            )}
          </div>

          <Separator className="my-2 bg-slate-200" />

          <Link href="/datasets" className={navItemClass(pathname === "/datasets")}>
            <Database className="w-4 h-4" />
            <span>数据源</span>
          </Link>

          <div>
            <Button
              type="button"
              onClick={() => setSemanticExpanded(!semanticExpanded)}
              variant="ghost"
              className="h-auto w-full justify-between px-3 py-2.5 font-medium text-slate-500 hover:bg-transparent hover:text-slate-800"
            >
              <div className="flex items-center gap-2">
                <BookOpen className="w-4 h-4" />
                <span>语义资产</span>
              </div>
              {semanticExpanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
            </Button>

            {semanticExpanded && (
              <div className="mt-1 space-y-0.5 pl-6">
                <Link href="/metrics" className={semanticItemClass(pathname === "/metrics")}>
                  <Sliders className="w-3.5 h-3.5" />
                  <span>指标管理</span>
                </Link>

                <Link href="/dimensions" className={semanticItemClass(pathname === "/dimensions")}>
                  <CheckSquare className="w-3.5 h-3.5" />
                  <span>维度管理</span>
                </Link>

                <Link href="/glossary" className={semanticItemClass(pathname === "/glossary")}>
                  <Tags className="w-3.5 h-3.5" />
                  <span>业务术语</span>
                </Link>

                <Button
                  type="button"
                  onClick={() => alert("分析模板模块正在准备上线中...")}
                  variant="ghost"
                  className="h-auto w-full justify-start gap-2 rounded-lg px-3 py-2 text-left text-xs font-medium text-slate-400 hover:bg-slate-200/20"
                >
                  <FileText className="w-3.5 h-3.5 text-slate-300" />
                  <span>分析模板</span>
                  <span className="text-[9px] bg-slate-200 text-slate-500 rounded px-1 scale-90">MOCK</span>
                </Button>
              </div>
            )}
          </div>
        </nav>
      </div>

      <div className="p-4 border-t border-[#e2e8f0] bg-white flex items-center justify-between relative">
        <div className="flex items-center gap-2.5">
          <Avatar className="size-9 border border-slate-200 bg-slate-100 text-slate-600">
            <AvatarFallback className="bg-slate-100">
              <User className="size-4" />
            </AvatarFallback>
          </Avatar>
          <div className="flex flex-col text-left">
            <span className="text-xs font-bold text-slate-800 font-sans">管理员</span>
            <span className="text-[10px] text-slate-400 font-sans">admin@insight.com</span>
          </div>
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger className="inline-flex size-7 items-center justify-center rounded text-slate-400 outline-none transition-colors hover:bg-slate-100 hover:text-slate-600">
            <ChevronDown className="size-4" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => alert("已查看管理员配置，当前为最高安全级别")}>
              查看管理员配置
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}
