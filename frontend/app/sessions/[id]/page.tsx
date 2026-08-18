import ChatSessionView from "@/components/insight-agent/chat-session-view";

interface SessionPageProps {
  params: Promise<{
    id: string;
  }>;
}

export default async function SessionPage({ params }: SessionPageProps) {
  /** 会话详情动态路由，从 URL 中读取 conversationId 并渲染聊天分析页面。 */

  const { id } = await params;
  return <ChatSessionView conversationId={id} />;
}
