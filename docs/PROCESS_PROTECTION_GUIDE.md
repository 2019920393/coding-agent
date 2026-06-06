# Codo 进程保护 - 快速指南

## 什么是进程保护？

Codo 内置了多层保护机制，防止 AI 意外终止 Codo 自身的进程，从而避免：
- 应用崩溃
- 丢失未保存的工作
- 会话中断

## 遇到端口占用怎么办？

### ❌ 错误做法

```bash
# 危险！可能会杀掉 Codo 自身
kill $(lsof -ti:3000)
taskkill /F /IM electron.exe
```

### ✅ 正确做法

1. **先识别占用进程**

Windows:
```bash
netstat -ano | findstr :3000
tasklist /FI "PID eq <进程ID>"
```

Linux/Mac:
```bash
lsof -i :3000
ps -p <进程ID> -o comm,args
```

2. **检查是否为 Codo 进程**
   - 进程名包含 "Codo"、"electron"、"ai_bridge"
   - 路径包含 "workbench-app"

3. **采取对应措施**

**如果是 Codo 进程：**
```
AI 会建议："端口被 Codo 自身占用，建议为你的应用使用其他端口（如 3001、4000、5000）"
```

**如果不是 Codo 进程：**
```
AI 会询问："端口被 <进程名> 占用，可以终止它吗？或选择其他端口？"
```

## 被保护的进程

Codo 保护以下进程模式：

| 进程类型 | 检测模式 | 示例 |
|---------|---------|------|
| Codo 主程序 | 名称包含 "codo" | Codo.exe, codo |
| Electron 进程 | 名称 "electron" 且路径包含 "Codo" | electron.exe (workbench-app) |
| AI 后端 | 命令行包含 "ai_bridge" | python ai_bridge.py |
| 开发服务器 | 路径包含 "workbench" | node (workbench-app) |

## 保护层次

```
┌─────────────────────────────────────┐
│   1. 系统提示：教育 AI 不要杀 Codo    │
└─────────────────────────────────────┘
              ↓
┌─────────────────────────────────────┐
│   2. 工具描述：强调进程安全规则       │
└─────────────────────────────────────┘
              ↓
┌─────────────────────────────────────┐
│   3. 运行时验证：阻止危险命令执行     │
└─────────────────────────────────────┘
```

## 错误消息示例

当 AI 尝试执行危险命令时，会看到：

```
⚠️ CRITICAL: This command attempts to kill a Codo-related process.
Terminating Codo processes will end your current session and destroy your work.

If you're trying to resolve a port conflict:
1. First identify the process: netstat -ano | findstr <port>
2. If it's a Codo process, use an alternate port for your application
3. If it's another process, ask the user for permission before killing it

Command blocked for your safety.
```

## 常见场景

### 场景 1：启动 Web 服务器遇到端口占用

**问题：** "启动 express 服务器时提示端口 3000 被占用"

**AI 正确做法：**
1. 运行 `netstat -ano | findstr :3000` 查看占用进程
2. 如果是 Codo 进程，建议使用 3001 端口
3. 如果不是，询问用户是否终止该进程

### 场景 2：启动 Electron 应用遇到端口占用

**问题：** "我的 Electron 小说阅读器启动失败，端口被占用"

**AI 正确做法：**
1. 检查端口占用情况
2. 发现是 Codo 的 Electron 进程占用
3. 建议："Codo 也是 Electron 应用，我们为你的小说阅读器换个端口吧，比如 8080？"

### 场景 3：清理无响应进程

**问题：** "我的开发服务器卡住了，帮我重启"

**AI 正确做法：**
1. 先识别进程：`ps aux | grep node`
2. 确认不是 Codo 的进程
3. 征得用户同意后终止：`kill -9 <PID>`

## 开发者注意事项

如果你在开发 Codo 插件或修改核心代码：

1. **不要禁用保护机制** - 这是关键的安全特性
2. **测试新命令** - 确保不会误杀 Codo 进程
3. **使用测试套件** - 运行 `pytest tests/tools/bash_tool/test_process_protection.py`
4. **查看日志** - 被阻止的命令会记录在日志中

## 故障排查

### Q: AI 一直不肯终止进程，即使不是 Codo 进程

**A:** 可能是保护逻辑过于严格。检查命令是否包含 "electron"、"python" 等关键词但不是 Codo 相关的。可以：
- 使用 PID 而不是进程名终止
- 明确告诉 AI 这不是 Codo 进程

### Q: AI 建议的替代端口也被占用了

**A:** 让 AI 使用系统自动分配的端口：
```javascript
const server = app.listen(0, () => {
  console.log(`Server running on port ${server.address().port}`);
});
```

### Q: 我想临时禁用保护测试某些功能

**A:** 不建议这样做。如果必须：
1. 备份当前会话
2. 直接在终端执行命令（不通过 AI）
3. 完成后立即重启 Codo

## 更多信息

详细的技术文档请参考：[PROCESS_PROTECTION.md](./PROCESS_PROTECTION.md)

## 反馈

如果遇到问题或有改进建议，请：
- 提交 Issue 描述问题
- 附上被阻止的命令和上下文
- 说明期望的行为
