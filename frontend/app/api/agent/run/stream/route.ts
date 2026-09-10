import { proxyToFastApi } from "../../../_proxy";

export async function POST(request: Request) {
  /** 将前端 Agent 流式请求原样转发到同路径的 FastAPI 路由。 */

  return proxyToFastApi(request, "agent/run/stream");
}
