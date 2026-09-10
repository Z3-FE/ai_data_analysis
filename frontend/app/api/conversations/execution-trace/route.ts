import { proxyToFastApi } from "../../_proxy";

export async function GET(request: Request) {
  /** 代理指定会话轮次的执行过程读取接口。 */

  return proxyToFastApi(request, "conversations/execution-trace");
}
