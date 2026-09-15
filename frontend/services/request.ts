/**
 * 统一的 HTTP 请求拦截器
 * - 请求拦截：添加全局 header、参数处理
 * - 响应拦截：错误处理、日志
 * - 只暴露 get 和 post 两种方法
 * - 参数使用 URL query string 或 POST body，不使用 {id} 路径参数
 */

export type QueryValue = string | number | boolean | null | undefined;

export type RequestOptions = {
  signal?: AbortSignal;
};

export type GetOptions = RequestOptions & {
  query?: Record<string, QueryValue>;
};

export type PostOptions = RequestOptions;

// 全局请求拦截器配置
interface Interceptors {
  request: Array<(config: RequestConfig) => RequestConfig | Promise<RequestConfig>>;
  response: Array<(response: Response, data: unknown) => unknown | Promise<unknown>>;
}

interface RequestConfig {
  path: string;
  method: string;
  headers?: Record<string, string>;
  body?: unknown;
  query?: Record<string, QueryValue>;
}

const interceptors: Interceptors = {
  request: [],
  response: [],
};

export function addRequestInterceptor(fn: (config: RequestConfig) => RequestConfig | Promise<RequestConfig>) {
  interceptors.request.push(fn);
}

export function addResponseInterceptor(fn: (response: Response, data: unknown) => unknown | Promise<unknown>) {
  interceptors.response.push(fn);
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  if (!query) return path;

  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== null && value !== undefined && value !== "") {
      params.set(key, String(value));
    }
  }
  const queryString = params.toString();
  return queryString ? `${path}?${queryString}` : path;
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) return null;

  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function getErrorMessage(data: unknown, status: number): string {
  if (typeof data === "string" && data.trim()) return data.trim();
  if (data && typeof data === "object" && !Array.isArray(data)) {
    const d = data as Record<string, unknown>;
    if (typeof d.detail === "string" && d.detail.trim()) return d.detail;
    if (typeof d.message === "string" && d.message.trim()) return d.message;
  }
  return `接口请求失败（HTTP ${status}）`;
}

async function applyRequestInterceptors(config: RequestConfig): Promise<RequestConfig> {
  let currentConfig = config;
  for (const interceptor of interceptors.request) {
    currentConfig = await interceptor(currentConfig);
  }
  return currentConfig;
}

async function applyResponseInterceptors(response: Response, data: unknown): Promise<unknown> {
  let currentData = data;
  for (const interceptor of interceptors.response) {
    currentData = await interceptor(response, currentData);
  }
  return currentData;
}

async function request<T>(
  path: string,
  init: RequestInit,
): Promise<T> {
  const response = await fetch(path, {
    ...init,
    cache: "no-store",
  });

  const data = await readBody(response);

  if (!response.ok) {
    throw new Error(getErrorMessage(data, response.status));
  }

  // 应用响应拦截器
  const processedData = await applyResponseInterceptors(response, data);
  return processedData as T;
}

/**
 * GET 请求
 * @param path 请求路径
 * @param options.query URL 查询参数，使用 ?key=value 格式
 */
export function get<T>(
  path: string,
  options: GetOptions = {},
): Promise<T> {
  return (async () => {
    let config: RequestConfig = {
      path,
      method: "GET",
      headers: { Accept: "application/json" },
      query: options.query,
    };

    // 应用请求拦截器
    config = await applyRequestInterceptors(config);

    const url = buildUrl(config.path, config.query);

    return request<T>(url, {
      method: config.method,
      headers: config.headers,
      signal: options.signal,
    });
  })();
}

/**
 * POST 请求
 * @param path 请求路径
 * @param body 请求体，会以 JSON 格式发送
 * @param options.query URL 查询参数（可选）
 */
export function post<T>(
  path: string,
  body: unknown,
  options: PostOptions & { query?: Record<string, QueryValue> } = {},
): Promise<T> {
  return (async () => {
    let config: RequestConfig = {
      path,
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body,
      query: options.query,
    };

    // 应用请求拦截器
    config = await applyRequestInterceptors(config);

    const url = buildUrl(config.path, config.query);

    return request<T>(url, {
      method: config.method,
      headers: config.headers,
      body: JSON.stringify(config.body),
      signal: options.signal,
    });
  })();
}

// ============ SSE 流式请求 ============

function parseSseBlock(block: string): unknown | undefined {
  const data = block
    .split(/\r?\n/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice("data:".length).trimStart())
    .join("\n");
  if (!data || data === "[DONE]") return undefined;

  try {
    return JSON.parse(data);
  } catch {
    throw new Error(`后端返回了无效的 SSE 数据：${data.trim().slice(0, 180)}`);
  }
}

/**
 * POST 流式请求 (SSE)
 */
export function postStream<T>(
  path: string,
  body: unknown,
  options: PostOptions = {},
): AsyncGenerator<T> {
  return (async function* () {
    let config: RequestConfig = {
      path,
      method: "POST",
      headers: {
        Accept: "text/event-stream",
        "Content-Type": "application/json",
      },
      body,
    };

    config = await applyRequestInterceptors(config);
    const url = buildUrl(config.path);

    const response = await fetch(url, {
      method: config.method,
      headers: config.headers,
      body: JSON.stringify(config.body),
      signal: options.signal,
    });

    if (!response.ok) {
      const data = await readBody(response);
      throw new Error(getErrorMessage(data, response.status));
    }
    if (!response.body) throw new Error("SSE 响应没有可读取的数据流。");

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split(/\r?\n\r?\n/);
        buffer = blocks.pop() ?? "";
        for (const block of blocks) {
          const event = parseSseBlock(block);
          if (event !== undefined) yield event as T;
        }
      }

      buffer += decoder.decode();
      const event = parseSseBlock(buffer);
      if (event !== undefined) yield event as T;
    } finally {
      reader.releaseLock();
    }
  })();
}
