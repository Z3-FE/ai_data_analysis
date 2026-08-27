"use client";

import React from "react";
import { Bell, ChevronDown, HelpCircle } from "lucide-react";
import { usePathname } from "next/navigation";
import Sidebar from "./sidebar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";

interface InsightAgentShellProps {
  children: React.ReactNode;
}

interface RouteMeta {
  routeLabel: string;
  moduleName: string;
  moduleDescription: string;
}

function getRouteMeta(pathname: string): RouteMeta {
  /** 根据当前路径返回 header 面包屑、模块名和模块说明。 */

  if (pathname === "/") {
    return {
      routeLabel: "分析工作站 / 新建会话",
      moduleName: "会话发起模块",
      moduleDescription: "选择数据源、上传语义文档、发起自然语言分析",
    };
  }

  if (pathname.startsWith("/sessions/")) {
    return {
      routeLabel: "分析画布",
      moduleName: "分析运行模块",
      moduleDescription: "展示 Agent 步骤、SQL、图表、来源和审计结果",
    };
  }

  if (pathname === "/datasets") {
    return {
      routeLabel: "语义资产 / 数据集说明",
      moduleName: "数据集资产模块",
      moduleDescription: "管理可查询表、字段说明、Join 路径和查询规则",
    };
  }

  if (pathname === "/metrics") {
    return {
      routeLabel: "语义资产 / 指标管理",
      moduleName: "指标口径模块",
      moduleDescription: "管理销售额、订单数、客单价等统一指标定义",
    };
  }

  if (pathname === "/dimensions") {
    return {
      routeLabel: "语义资产 / 维度管理",
      moduleName: "维度映射模块",
      moduleDescription: "管理品类、地区、时间、支付方式等分析维度",
    };
  }

  if (pathname === "/glossary") {
    return {
      routeLabel: "语义资产 / 业务术语",
      moduleName: "业务术语模块",
      moduleDescription: "管理自然语言术语、别名、分组规则和业务规则",
    };
  }

  return {
    routeLabel: "Insight Agent",
    moduleName: "未识别模块",
    moduleDescription: "当前路径暂未配置模块说明",
  };
}

export default function InsightAgentShell({ children }: InsightAgentShellProps) {
  /** Insight Agent 全局外壳：渲染左侧菜单、顶部 header 和当前页面内容。 */

  const pathname = usePathname();
  const routeMeta = getRouteMeta(pathname);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-slate-50 text-slate-800 font-sans font-medium selection:bg-blue-500/10 selection:text-blue-600">
      {/* 左侧菜单 */}
      <Sidebar />

      <div className="flex-1 flex flex-col overflow-hidden min-w-0">
           {/* header部分 */}
        <header className="h-[60px] border-b border-[#e2e8f0] bg-white px-6 shrink-0 flex items-center justify-between select-none">
       
          <div className="min-w-0 flex items-center gap-3">
            <div className="flex items-center gap-1.5 text-xs text-slate-400 font-bold min-w-0">
              <span className="text-slate-800 font-extrabold tracking-tight shrink-0">Insight Agent</span>
              <span className="text-slate-300 shrink-0">/</span>
              {/* 面包屑 */}
              <span className="text-blue-600 font-extrabold truncate">{routeMeta.routeLabel}</span>
            </div>

            <Badge variant="outline" className="hidden shrink-0 gap-2 rounded-full border-blue-100 bg-blue-50 px-3 py-1 text-[11px] font-black text-blue-700 lg:flex">
              <span className="text-blue-400">模块</span>
              <span>{routeMeta.moduleName}</span>
            </Badge>

            <div className="hidden 2xl:block truncate text-[11px] font-semibold text-slate-400 max-w-[360px]">
              {routeMeta.moduleDescription}
            </div>
          </div>

          <div className="flex items-center gap-5">
            <Button
              type="button"
              onClick={() => {
                alert("已启动 Insight Agent 的实时人工智能帮助专家。");
              }}
              variant="ghost"
              size="sm"
              className="h-8 gap-1 rounded-lg px-2 text-xs font-bold text-slate-500 hover:bg-slate-50 hover:text-slate-800"
            >
              <HelpCircle className="text-slate-400" />
              <span>帮助中心</span>
            </Button>

            <div className="relative">
              <Button
                type="button"
                onClick={() => alert("您有 3 条新的语义映射、数据库指标审核建议。已自动归于最近审查列表中。")}
                variant="ghost"
                size="icon"
                className="relative size-8 rounded-full text-slate-400 hover:bg-slate-50 hover:text-slate-700"
              >
                <Bell />
                <span className="absolute -top-0.5 -right-0.5 bg-red-500 text-white font-black text-[9px] w-4 h-4 rounded-full flex items-center justify-center border-2 border-white scale-90">
                  3
                </span>
              </Button>
            </div>

            <DropdownMenu>
              <DropdownMenuTrigger className="ml-1 inline-flex h-8 items-center justify-center gap-1 rounded-lg px-2 outline-none transition-colors hover:bg-slate-50">
                <Avatar className="size-6 border border-blue-200 bg-blue-100 text-blue-600">
                  <AvatarFallback className="bg-blue-100 text-xs font-bold text-blue-600">A</AvatarFallback>
                </Avatar>
                <span className="ml-1 text-xs font-extrabold text-slate-700">管理员</span>
                <ChevronDown className="size-3.5 text-slate-400" />
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => alert("当前会话身份权限：[ 语义层管理员 & 企业风控总监 ]")}>
                  查看身份权限
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>
          {/* 主页面 */}
        <main className="flex-1 flex overflow-hidden min-h-0 min-w-0 bg-[#fafbfc]">
          {children}
        </main>
      </div>
    </div>
  );
}
