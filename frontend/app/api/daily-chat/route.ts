import { proxyToFastApi } from "../_proxy";

export async function POST(request: Request) {
  /** 代理独立日常聊天接口，当前聊天页面不进入数据分析 Agent 图。 */

  return proxyToFastApi(request, "daily-chat");
}
