const GET_REPLAY_WINDOW_MS = 1000;
const inFlightGetRequests = new Map<string, Promise<any>>();
const recentGetResponses = new Map<string, { data: any; expiresAt: number }>();

type ApiQueryValue = string | number | boolean | null | undefined;
type ApiQuery = Record<string, ApiQueryValue>;

async function readResponseBody(response: Response): Promise<any> {
  /** 同时支持 JSON 和纯文本错误，避免把网关/后端错误伪装成 JSON 解析异常。 */
  const text = await response.text();
  if (!text) return null;

  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function getResponseError(data: any, status: number) {
  /** 从结构化或纯文本响应中提取可读的接口错误。 */
  if (typeof data === "string" && data.trim()) return data.trim();
  if (data && typeof data === "object" && typeof data.detail === "string") return data.detail;
  return "接口请求失败（HTTP " + status + "）";
}

export function buildApiUrl(path: string, query: ApiQuery = {}) {
  /** 把请求参数统一编码到 query string，API 路由本身保持固定。 */

  const searchParams = new URLSearchParams();

  Object.entries(query).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      searchParams.set(key, String(value));
    }
  });

  const queryString = searchParams.toString();
  return queryString ? `${path}?${queryString}` : path;
}

export function invalidateApiCache(path?: string) {
  /** 清理前端 GET 请求短缓存，常用于 POST 成功后刷新列表数据。 */

  if (!path) {
    inFlightGetRequests.clear();
    recentGetResponses.clear();
    return;
  }

  inFlightGetRequests.delete(path);
  recentGetResponses.delete(path);
}

export async function apiGet(path: string, query: ApiQuery = {}): Promise<any> {
  /** 发起 GET 请求，并合并短时间内重复触发的相同请求。 */

  const url = buildApiUrl(path, query);
  const now = Date.now();
  const recent = recentGetResponses.get(url);

  if (recent && recent.expiresAt > now) {
    return recent.data;
  }

  if (recent) {
    recentGetResponses.delete(url);
  }

  const inFlight = inFlightGetRequests.get(url);

  if (inFlight) {
    return inFlight;
  }

  // React dev Strict Mode can run effects twice; collapse duplicate GETs here.
  const request = fetch(url, {
    cache: "no-store",
    headers: {
      Accept: "application/json",
    },
  })
    .then(async (response) => {
      const data = await readResponseBody(response);

      if (!response.ok) {
        throw new Error(getResponseError(data, response.status));
      }

      recentGetResponses.set(url, {
        data,
        expiresAt: Date.now() + GET_REPLAY_WINDOW_MS,
      });

      return data;
    })
    .finally(() => {
      inFlightGetRequests.delete(url);
    });

  inFlightGetRequests.set(url, request);

  return request;
}

export async function apiPost(path: string, body: unknown): Promise<any> {
  /** 发起 POST 请求，成功后清理 GET 缓存，保证列表和详情重新拉取。 */

  const response = await fetch(path, {
    method: "POST",
    cache: "no-store",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });
  const data = await readResponseBody(response);

  if (!response.ok) {
    throw new Error(getResponseError(data, response.status));
  }

  invalidateApiCache();

  return data;
}
