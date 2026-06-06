import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  // 1. 插件：启用 React 支持
  plugins: [react()],

  // 2. 开发服务器配置（npm run dev 时生效）
  server: {
    host: "127.0.0.1",   // 只允许本机访问
    port: 5173,         // 开发端口固定 5173
    strictPort: true    // 端口被占用就直接报错，不自动换端口
  },

  // 3. 预览服务器配置（npm run preview 时生效）
  preview: {
    host: "127.0.0.1",   // 本机访问
    port: 4173,         // 预览端口固定 4173
    strictPort: true    // 端口占用直接报错
  },

  // 4. 生产构建配置（npm run build 时生效）
  build: {
    outDir: "dist",     // 打包产物输出到 dist 文件夹
    emptyOutDir: true   // 每次打包前清空 dist 文件夹
  }
});