import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8765",
    },
    fs: {
      // 分时 / K 线图表代码在同级 market-data-hub/web，和 longtou-web 共用；开发服务默认不读项目目录以外的文件
      allow: [".", "../../market-data-hub/web"],
    },
  },
});
