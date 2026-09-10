/**
 * 解析应用后端返回的时间。
 *
 * 会话历史当前由后端以 UTC 的无时区字符串写入 MySQL DATETIME，浏览器如果
 * 直接解析会把它误认为本地时间，因此这里明确按 UTC 解释后再交给浏览器转换。
 */
export function parseBackendDate(value?: Date | string | null): Date | undefined {
  if (!value) return undefined;
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? undefined : value;
  }

  const text = value.trim().replace(" ", "T");
  if (!text) return undefined;
  const normalizedText = text.replace(/(\.\d{3})\d+(?=(?:Z|[+-]\d{2}:?\d{2})?$)/, "$1");
  const hasTimezone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(normalizedText);
  const date = new Date(hasTimezone ? normalizedText : normalizedText + "Z");
  return Number.isNaN(date.getTime()) ? undefined : date;
}
