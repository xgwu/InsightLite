import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 开发代理：/api/* -> 本机 FastAPI(8000)，前端代码统一以 /api 为前缀
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
