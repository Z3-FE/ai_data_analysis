import { proxyToFastApi } from "../_proxy";

export async function GET(request: Request) {
  /** 返回当前浏览器会话的轻量状态；会话 ID 通过 query 参数传递。 */

  const sessionId = new URL(request.url).searchParams.get("session_id") || "";
  return Response.json({
    session_id: sessionId,
    status: "ready",
    execution_endpoint: "POST /api/analysis",
  });
}

export async function POST(request: Request) {
  /** 固定分析入口：提交问题并透传当前 Agent 的 SSE 响应。 */

  return proxyToFastApi(request, "agent/run/stream");
}
