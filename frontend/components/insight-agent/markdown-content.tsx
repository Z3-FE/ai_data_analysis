"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * AI 回复正文的 markdown 渲染：AST 输出、默认不放行原始 HTML（XSS 安全）。
 * 样式对齐面板的 slate 体系：行内代码是浅灰 chip，代码块复用 EventDetails 的深底 pre。
 */
export function MarkdownContent({ text }: { text: string }) {
  return (
    <div className="min-w-0 text-sm leading-6 text-slate-700 [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ node, ...props }) => <h1 className="mt-5 mb-2 text-base font-extrabold tracking-tight text-slate-900" {...props} />,
          h2: ({ node, ...props }) => <h2 className="mt-5 mb-2 text-[15px] font-extrabold tracking-tight text-slate-900" {...props} />,
          h3: ({ node, ...props }) => <h3 className="mt-4 mb-1.5 text-sm font-extrabold text-slate-900" {...props} />,
          h4: ({ node, ...props }) => <h4 className="mt-3 mb-1 text-sm font-bold text-slate-800" {...props} />,
          p: ({ node, ...props }) => <p className="my-2" {...props} />,
          strong: ({ node, ...props }) => <strong className="font-semibold text-slate-900" {...props} />,
          a: ({ node, ...props }) => <a className="text-blue-600 underline underline-offset-2 hover:text-blue-700" target="_blank" rel="noreferrer" {...props} />,
          ul: ({ node, ...props }) => <ul className="my-2 list-disc space-y-1 pl-5 marker:text-slate-400" {...props} />,
          ol: ({ node, ...props }) => <ol className="my-2 list-decimal space-y-1 pl-5 marker:text-slate-400" {...props} />,
          li: ({ node, ...props }) => <li className="pl-0.5" {...props} />,
          blockquote: ({ node, ...props }) => <blockquote className="my-2 border-l-2 border-slate-300 pl-3 text-slate-500" {...props} />,
          hr: ({ node, ...props }) => <hr className="my-4 border-slate-200" {...props} />,
          img: ({ node, ...props }) => <img className="max-w-full rounded-lg" {...props} />,
          del: ({ node, ...props }) => <del className="text-slate-400" {...props} />,
          // 行内代码统一浅灰 chip；代码块由 pre 覆盖回深底样式。
          code: ({ node, className, ...props }) => (
            <code className={"rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[0.85em] text-slate-800 " + (className ?? "")} {...props} />
          ),
          pre: ({ node, ...props }) => (
            <pre
              className="my-2 overflow-x-auto rounded-lg bg-slate-950 p-3 text-xs leading-5 text-slate-100 [&_code]:rounded-none [&_code]:bg-transparent [&_code]:p-0 [&_code]:text-inherit"
              {...props}
            />
          ),
          table: ({ node, ...props }) => (
            <div className="my-3 overflow-x-auto">
              <table className="w-full border-collapse text-xs" {...props} />
            </div>
          ),
          th: ({ node, ...props }) => <th className="border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-left font-bold text-slate-600" {...props} />,
          td: ({ node, ...props }) => <td className="border border-slate-200 px-2.5 py-1.5 align-top" {...props} />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
