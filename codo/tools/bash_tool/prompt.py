from codo.constants import BASH_TIMEOUT_DEFAULT_MS, BASH_TIMEOUT_MAX_MS

BASH_TOOL_NAME = "Bash"

DESCRIPTION = f"""执行 shell 命令并返回输出。

shell 环境从用户配置文件（bash 或 zsh）初始化。工作目录在命令之间保持不变，但 shell 状态不会保留。

**重要提示**：避免使用此工具运行 `find`、`grep`、`cat`、`head`、`tail`、`sed`、`awk` 或 `echo` 命令，除非明确指示或验证专用工具无法完成任务。请使用适当的专用工具，因为这将为用户提供更好的体验：

- 文件搜索：使用 Glob（不要用 find 或 ls）
- 内容搜索：使用 Grep（不要用 grep 或 rg）
- 读取文件：使用 Read（不要用 cat/head/tail）
- 编辑文件：使用 Edit（不要用 sed/awk）
- 写入文件：使用 Write（不要用 echo >/cat <<EOF）
- 通信：直接输出文本（不要用 echo/printf）

虽然 Bash 工具可以做类似的事情，但最好使用内置工具，因为它们提供更好的用户体验，并使审查工具调用和授予权限更容易。

## 使用说明

- 如果命令会创建新目录或文件，请先使用此工具运行 `ls` 验证父目录存在且位置正确
- 始终用双引号包裹包含空格的文件路径（例如 cd "path with spaces/file.txt"）
- 尽量在整个会话中保持当前工作目录，使用绝对路径并避免使用 `cd`。如果用户明确要求，可以使用 `cd`
- 可以指定可选的超时时间（毫秒，最多 {BASH_TIMEOUT_MAX_MS}ms）。默认情况下，命令将在 {BASH_TIMEOUT_DEFAULT_MS}ms 后超时
- 可以使用 `run_in_background` 参数在后台运行命令。仅在不需要立即获得结果且可以稍后收到完成通知时使用。不需要在命令末尾使用 '&'
- 发出多个命令时：
  - 如果命令是独立的且可以并行运行，在单个消息中进行多个 Bash 工具调用。例如：如果需要运行 "git status" 和 "git diff"，发送包含两个并行 Bash 工具调用的单个消息
  - 如果命令相互依赖且必须按顺序运行，使用单个 Bash 调用并用 '&&' 将它们链接在一起
  - 仅在需要按顺序运行命令但不关心早期命令是否失败时使用 ';'
  - 不要使用换行符分隔命令（引号字符串中的换行符可以）

## Git 命令

- 优先创建新提交而不是修改现有提交
- 在运行破坏性操作之前（例如 git reset --hard、git push --force、git checkout --），考虑是否有更安全的替代方案可以实现相同目标。仅在真正是最佳方法时使用破坏性操作
- 除非用户明确要求，否则不要跳过钩子（--no-verify）或绕过签名（--no-gpg-sign、-c commit.gpgsign=false）。如果钩子失败，调查并修复根本问题

Use the gh command via the Bash tool for other GitHub-related tasks including working with issues, checks, and releases. If given a Github URL use the gh command to get the information needed.

# Other common operations
- View comments on a Github PR: gh api repos/foo/bar/pulls/123/comments

## 避免不必要的 sleep 命令

- 不要在可以立即运行的命令之间 sleep - 直接运行它们
- 如果命令长时间运行且希望在完成时收到通知 - 使用 `run_in_background`。不需要 sleep
- 不要在 sleep 循环中重试失败的命令 - 诊断根本原因
- 如果等待使用 `run_in_background` 启动的后台任务，将在完成时收到通知 - 不要轮询
- 如果必须轮询外部进程，使用检查命令（例如 `gh run view`）而不是先 sleep
- 如果必须 sleep，保持持续时间短（1-5 秒）以避免阻塞用户

## ⚠️ 进程安全规则

**CRITICAL - 绝对禁止终止 Codo 相关进程**：
- **NEVER** 使用 taskkill、kill 或任何命令终止包含以下关键词的进程：
  - "Codo" 或 "codo"（Codo 主应用）
  - "electron" 且路径包含 "Codo"（Codo 的 Electron 进程）
  - "python" 且命令行包含 "ai_bridge"（Codo 的 AI 后端）
  - "node" 且路径包含 "Codo"（Codo 的前端开发服务器）

- **端口占用处理原则**：
  - 如果端口被占用，首先使用 `netstat -ano | findstr <端口>` 或 `lsof -i :<端口>` 查看占用进程的详细信息
  - 检查进程名称和路径，**绝对不要**杀掉 Codo 相关进程
  - 如果是 Codo 进程占用端口：
    - 主动询问用户："端口被 Codo 自身进程占用，是否需要为你的应用换一个端口？"
    - 建议使用其他端口（如 3001、4000、5000、8081 等）
    - **NEVER** 自动杀掉进程
  - 如果是其他进程占用端口：
    - 告知用户具体是哪个进程占用
    - 征求用户同意后才能终止该进程
    - 使用 `taskkill /PID <进程ID>` 而不是 `taskkill /IM <进程名>`，避免误杀

- **安全检查清单**：
  在执行任何 kill/taskkill 命令前，必须：
  1. ✓ 确认进程 PID 和名称
  2. ✓ 确认进程路径不包含 "Codo"
  3. ✓ 确认不是 Python/Node/Electron 且与 Codo 相关
  4. ✓ 已获得用户明确同意

**违反此规则将导致用户丢失工作，这是不可接受的！**
"""

def get_user_facing_name() -> str:
    """获取用户可见的工具名称"""
    return "执行命令"

def get_tool_use_summary(input_data: dict) -> str:
    """
    获取工具使用摘要

    Args:
        input_data: 工具输入数据

    Returns:
        摘要字符串
    """
    command = input_data.get('command', '')
    description = input_data.get('description')

    if description:
        return description

    # 截断长命令
    if len(command) > 60:
        return f"{command[:60]}..."

    return command

def get_activity_description(input_data: dict) -> str:
    """
    获取活动描述（用于进度显示）

    Args:
        input_data: 工具输入数据

    Returns:
        活动描述
    """
    summary = get_tool_use_summary(input_data)
    return f"执行: {summary}"
