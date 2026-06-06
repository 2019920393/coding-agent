# Codo 进程保护机制

## 问题描述

当用户要求 AI 启动应用程序（如 Electron 小说阅读器）时，如果遇到端口占用，AI 可能会尝试终止占用端口的进程。然而，如果该端口被 Codo 自身的进程占用（如 Electron 主进程或 AI Bridge Python 后端），终止这些进程将导致：

- Codo 应用崩溃
- 用户丢失所有未保存的工作
- 当前会话中断
- 对话上下文丢失

这是一个**严重的用户体验问题**，必须通过多层保护机制来防止。

## 解决方案

### 1. 系统提示层保护

在 `codo/constants.py` 的 `get_actions_section()` 中添加了明确的进程保护规则：

**保护的进程模式：**
- 进程名包含 "Codo" 或 "codo"（不区分大小写）
- Electron 进程且路径包含 "Codo" 或 "workbench-app"
- Python 进程且命令行包含 "ai_bridge.py" 或 "ai_bridge"
- Node.js 进程且路径包含 "Codo" 或 "workbench-app"
- 监听 AI bridge 控制端口的进程（通常在 30000-40000 范围）

**安全的端口冲突解决流程：**

1. **识别进程**：使用 `netstat` 或 `lsof` 查看占用端口的进程
2. **检查是否为 Codo 进程**：
   - 如果是：建议用户为其应用选择备用端口
   - 如果不是：征求用户同意后再终止
3. **仅在用户批准后终止**：确保不是 Codo 相关进程

### 2. Bash 工具描述层保护

在 `codo/tools/bash_tool/prompt.py` 中添加了详细的进程安全规则：

```python
## ⚠️ 进程安全规则

**CRITICAL - 绝对禁止终止 Codo 相关进程**：
- **NEVER** 使用 taskkill、kill 或任何命令终止包含以下关键词的进程：
  - "Codo" 或 "codo"（Codo 主应用）
  - "electron" 且路径包含 "Codo"（Codo 的 Electron 进程）
  - "python" 且命令行包含 "ai_bridge"（Codo 的 AI 后端）
  - "node" 且路径包含 "Codo"（Codo 的前端开发服务器）
```

### 3. 代码执行层保护

在 `codo/tools/bash_tool/bash_tool.py` 的 `validate_input()` 方法中添加了运行时验证：

**检测逻辑：**
1. 检测命令是否包含进程终止操作（taskkill, kill, pkill, killall）
2. 如果是终止命令，检查目标是否为 Codo 相关进程
3. 如果目标是 Codo 进程，**直接阻止执行**并返回详细的错误消息

**错误消息包含：**
- 严重性警告（CRITICAL）
- 解释后果（终止会话、丢失工作）
- 安全的替代方案（使用备用端口）
- 正确的处理流程

## 测试覆盖

创建了完整的测试套件 `tests/tools/bash_tool/test_process_protection.py`，包含：

1. **阻止危险命令**：验证所有可能终止 Codo 进程的命令都被阻止
2. **允许安全命令**：确保不误伤合法的进程管理操作
3. **大小写不敏感**：防止通过大小写绕过保护
4. **有用的错误消息**：确保用户知道如何正确处理端口冲突

## 使用示例

### ❌ 被阻止的命令

```bash
# 终止 Codo 主进程
taskkill /F /IM Codo.exe

# 终止包含 Codo 的 Electron 进程
kill -9 $(ps aux | grep 'Codo' | awk '{print $2}')

# 终止 AI Bridge 后端
pkill -f ai_bridge.py

# 终止占用 AI Bridge 端口的进程
kill $(lsof -ti:30000)
```

**系统响应：**
```
⚠️ CRITICAL: This command attempts to kill a Codo-related process.
Terminating Codo processes will end your current session and destroy your work.

If you're trying to resolve a port conflict:
1. First identify the process: netstat -ano | findstr <port>
2. If it's a Codo process, use an alternate port for your application
3. If it's another process, ask the user for permission before killing it

Command blocked for your safety.
```

### ✅ 正确的处理流程

**场景：启动应用时遇到端口 3000 被占用**

```bash
# 1. 首先识别占用进程
netstat -ano | findstr :3000
# 或 (Linux/Mac)
lsof -i :3000

# 2. 检查进程详情
tasklist /FI "PID eq 12345"
# 或
ps -p 12345 -o comm,args

# 3a. 如果是 Codo 进程
# AI 应该回复：
# "端口 3000 被 Codo 自身进程占用。我建议为你的应用使用备用端口，
# 比如 3001、4000 或 5000。你想使用哪个端口？"

# 3b. 如果不是 Codo 进程
# AI 应该回复：
# "端口 3000 被 <进程名> (PID 12345) 占用。
# 可以为你终止这个进程吗？或者我们可以为你的应用选择其他端口。"
```

### ✅ 允许的命令

```bash
# 终止特定 PID（已确认不是 Codo）
taskkill /F /PID 12345

# 终止其他应用
taskkill /F /IM notepad.exe
pkill -9 chrome

# 检查命令（不终止进程）
netstat -ano
lsof -i :3000
ps aux | grep node
```

## 保护层次

| 层次 | 位置 | 作用 | 触发时机 |
|------|------|------|----------|
| **系统提示** | `constants.py` | 教育 AI 不要终止 Codo 进程 | AI 推理时 |
| **工具描述** | `bash_tool/prompt.py` | 强调进程安全规则 | AI 使用 Bash 工具时 |
| **运行时验证** | `bash_tool/bash_tool.py` | 阻止危险命令执行 | 命令执行前 |

这种**多层防御**策略确保即使某一层失效，其他层仍能提供保护。

## 监控和日志

当检测到进程终止命令时（即使未被阻止），系统会记录警告日志：

```python
logger.warning(f"Process termination command detected: {input_data.command}")
```

这有助于：
- 监控误报情况
- 分析 AI 行为模式
- 改进保护策略

## 未来改进方向

1. **动态进程识别**：
   - 在运行时获取 Codo 进程的实际 PID
   - 更精确地判断命令是否会影响 Codo

2. **用户配置**：
   - 允许用户配置额外的受保护进程
   - 自定义保护规则

3. **智能提示**：
   - 当 AI 尝试执行被阻止的命令时，自动建议替代方案
   - 提供一键切换端口的选项

4. **审计日志**：
   - 记录所有被阻止的命令尝试
   - 生成安全报告

## 贡献

如果发现新的绕过方式或需要保护的场景，请：

1. 在 `test_process_protection.py` 中添加测试用例
2. 更新 `bash_tool.py` 中的保护逻辑
3. 更新系统提示中的规则说明
4. 更新本文档

## 参考

- [系统提示模板](../codo/constants.py#L149)
- [Bash 工具实现](../codo/tools/bash_tool/bash_tool.py#L102)
- [Bash 工具描述](../codo/tools/bash_tool/prompt.py#L47)
- [测试套件](../tests/tools/bash_tool/test_process_protection.py)
