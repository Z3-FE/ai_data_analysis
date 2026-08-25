import AnalysisWorkspace from "@/components/insight-agent/analysis-workspace";

export default function HomePage() {
  /** 固定首页直接承载当前会话，避免通过动态 URL 切换分析上下文。 */

  return <AnalysisWorkspace />;
}
