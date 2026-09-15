import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  turbopack: {
    root: process.cwd(),
    rules: {
      "**/*.{tsx,jsx}": {
        loaders: [
          {
            loader: "@locator/webpack-loader",
            options: { env: "development" },
          },
        ],
      },
    },
  },

  // API 代理配置：将 /api/* 请求统一转发到 FastAPI 后端
  async rewrites() {
    const FASTAPI_BASE_URL = process.env.FASTAPI_BASE_URL ?? "http://127.0.0.1:8000";

    return {
      /**
       * 通配符匹配：所有 /api/* 请求直接转发到 FastAPI
       * - source: /api/conversations/list?user_id=xxx
       * - destination: http://127.0.0.1:8000/api/conversations/list?user_id=xxx
       */
      beforeFiles: [
        {
          source: "/api/:path*",
          destination: `${FASTAPI_BASE_URL}/api/:path*`,
        },
      ],
    };
  },
};

export default nextConfig;
