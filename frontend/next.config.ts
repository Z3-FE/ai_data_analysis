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
};

export default nextConfig;
