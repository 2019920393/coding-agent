"""
BashTool 辅助函数

提供命令解析、只读检测等功能。
"""

import re
from typing import List, Set

# 只读命令列表
READONLY_COMMANDS: Set[str] = {
    'ls', 'cat', 'head', 'tail', 'grep', 'find', 'wc', 'sort', 'uniq',
    'diff', 'less', 'more', 'file', 'stat', 'du', 'df', 'pwd', 'which',
    'whereis', 'whoami', 'id', 'groups', 'env', 'printenv', 'echo',
    'printf', 'date', 'cal', 'uptime', 'uname', 'hostname', 'ps', 'top',
    'git status', 'git log', 'git diff', 'git show', 'git branch',
    'git remote', 'git tag', 'npm list', 'pip list', 'pip show'
}

def parseCommand(command: str) -> List[str]:
    """
    解析复合命令（&&, ||, ;, |）

    Args:
        command: 命令字符串

    Returns:
        子命令列表

    Examples:
        >>> parseCommand("ls -la && cat file.txt")
        ['ls -la', 'cat file.txt']
        >>> parseCommand("echo hello | grep h")
        ['echo hello', 'grep h']
    """
    # 简单实现：按分隔符拆分
    # 注意：这不处理引号内的分隔符
    parts = re.split(r'[;&|]+', command)
    return [part.strip() for part in parts if part.strip()]

def isReadOnlyCommand(command: str) -> bool:
    """
    检测命令是否为只读操作

    Args:
        command: 命令字符串

    Returns:
        是否为只读命令

    Examples:
        >>> isReadOnlyCommand("ls -la")
        True
        >>> isReadOnlyCommand("rm file.txt")
        False
        >>> isReadOnlyCommand("git status && git diff")
        True
    """
    # 解析复合命令
    sub_commands = parseCommand(command)

    for sub_cmd in sub_commands:
        # 提取命令名（第一个单词）
        parts = sub_cmd.split()
        if not parts:
            continue

        cmd_name = parts[0]

        # 检查是否在只读列表中
        if cmd_name not in READONLY_COMMANDS:
            # 检查是否为 git 子命令
            if cmd_name == 'git' and len(parts) > 1:
                git_subcmd = f"git {parts[1]}"
                if git_subcmd not in READONLY_COMMANDS:
                    return False
            else:
                return False

    return True

def extractCommandName(command: str) -> str:
    """
    提取命令名称（用于权限匹配）

    Args:
        command: 命令字符串

    Returns:
        命令名称

    Examples:
        >>> extractCommandName("ls -la /tmp")
        'ls'
        >>> extractCommandName("git status")
        'git status'
    """
    parts = command.strip().split()
    if not parts:
        return ''

    cmd_name = parts[0]

    # 特殊处理 git 命令
    if cmd_name == 'git' and len(parts) > 1:
        return f"git {parts[1]}"

    return cmd_name

def sanitizeCommand(command: str) -> str:
    """
    清理命令字符串（移除危险字符）

    Args:
        command: 命令字符串

    Returns:
        清理后的命令
    """
    # 暂时不做清理，保持原样
    # 未来可以添加危险字符检测
    return command

def formatCommandOutput(stdout: str, stderr: str, max_length: int = 10000) -> str:
    """
    格式化命令输出（用于显示）

    Args:
        stdout: 标准输出
        stderr: 标准错误
        max_length: 最大长度

    Returns:
        格式化的输出
    """
    parts = []

    if stdout:
        parts.append("=== 标准输出 ===")
        if len(stdout) > max_length:
            parts.append(stdout[:max_length])
            parts.append(f"\n... (截断，共 {len(stdout)} 字符)")
        else:
            parts.append(stdout)

    if stderr:
        if parts:
            parts.append("\n")
        parts.append("=== 标准错误 ===")
        if len(stderr) > max_length:
            parts.append(stderr[:max_length])
            parts.append(f"\n... (截断，共 {len(stderr)} 字符)")
        else:
            parts.append(stderr)

    return '\n'.join(parts)
