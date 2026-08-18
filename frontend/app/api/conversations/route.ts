import { proxyToFastApi } from "../_proxy";

export async function GET(request: Request) {
  /** 代理查询会话列表接口。 */

  return proxyToFastApi(request, "conversations");
}

export async function POST(request: Request) {
  /** 代理创建会话接口。 */

  return proxyToFastApi(request, "conversations");
}
