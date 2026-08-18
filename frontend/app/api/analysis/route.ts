import { proxyToFastApi } from "../_proxy";

export async function POST(request: Request) {
  /** 固定分析入口：提交问题并直接透传 FastAPI 的 SSE 响应。 */

  return proxyToFastApi(request, "analysis");
}
