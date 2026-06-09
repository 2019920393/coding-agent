import { defineConfig } from 'tsup';

export default defineConfig({
  entry: ['electron/main.ts', 'electron/preload.ts'],
  format: ['cjs'],
  platform: 'node',
  external: ['electron'],
  outDir: 'dist-electron',
  clean: true,
  noExternal: [],
  // 禁用 ESM 互操作性，防止 __toESM 转换
  shims: false,
  // 确保不尝试解析 electron 模块
  banner: {
    js: `// @ts-nocheck`,
  },
});
