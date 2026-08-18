import { proxyToFastApi } from "../_proxy";

export async function GET(request: Request) {
  /** 固定语义资产入口：通过 asset_type 参数选择资产类型。 */

  return proxyToFastApi(request, "semantic");
}
