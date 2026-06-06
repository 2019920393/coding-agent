# 🛡️ 进程保护机制已启用

## 快速说明

Codo 现在具有多层进程保护机制，防止 AI 意外终止 Codo 自身的进程。

### 保护范围

- ✅ Codo 主程序 (Codo.exe)
- ✅ Electron 进程
- ✅ AI Bridge Python 后端
- ✅ 开发服务器

### 遇到端口占用？

**正确做法：**
1. 先识别占用进程（netstat/lsof）
2. 如果是 Codo 进程 → AI 会建议使用其他端口
3. 如果不是 Codo 进程 → AI 会征求你的同意再终止

**AI 会自动处理，你不需要担心！**

### 文档

- [详细技术文档](./docs/PROCESS_PROTECTION.md)
- [用户指南](./docs/PROCESS_PROTECTION_GUIDE.md)

### 测试

```bash
pytest tests/tools/bash_tool/test_process_protection.py -v
```

---

**为什么需要这个？**

之前用户报告，当让 AI 启动应用时遇到端口占用，AI 会直接终止占用端口的进程。如果该进程恰好是 Codo 自身（比如 Electron 主进程或 AI Bridge），就会导致整个应用崩溃，用户丢失所有未保存的工作。

现在这个问题已经彻底解决！🎉
