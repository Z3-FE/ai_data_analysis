const FASTAPI_BASE_URL = process.env.FASTAPI_BASE_URL ?? "http://127.0.0.1:8000";

const HOP_BY_HOP_HEADERS = new Set([
  "connection",
  "content-encoding",
  "content-length",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
]);

function buildTargetUrl(request: Request, endpoint: string) {
  /** 根据固定业务入口拼出真实 FastAPI 后端地址。 */

  const incomingUrl = new URL(request.url);
  const targetUrl = new URL(`/api/${endpoint}`, FASTAPI_BASE_URL);
  targetUrl.search = incomingUrl.search;
  return targetUrl;
}

function buildForwardHeaders(request: Request) {
  /** 只转发必要请求头，避免浏览器/代理专用头影响后端。 */

  const headers = new Headers();
  const contentType = request.headers.get("content-type");
  const accept = request.headers.get("accept");

  if (contentType) headers.set("content-type", contentType);
  if (accept) headers.set("accept", accept);

  return headers;
}

function buildResponseHeaders(upstreamHeaders: Headers) {
  /** 过滤 hop-by-hop 响应头，生成可安全返回给浏览器的 headers。 */

  const headers = new Headers();

  upstreamHeaders.forEach((value, key) => {
    if (!HOP_BY_HOP_HEADERS.has(key.toLowerCase())) {
      headers.set(key, value);
    }
  });

  return headers;
}

export async function proxyToFastApi(request: Request, endpoint: string) {
  /** Next.js API 统一代理入口，把前端请求转发到 FastAPI。 */

  const method = request.method.toUpperCase();
  const hasBody = method !== "GET" && method !== "HEAD";
  const targetUrl = buildTargetUrl(request, endpoint);

  const upstream = await fetch(targetUrl, {
    method,
    headers: buildForwardHeaders(request),
    body: hasBody ? await request.arrayBuffer() : undefined,
    cache: "no-store",
    signal: request.signal,
  });

  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: buildResponseHeaders(upstream.headers),
  });
}
