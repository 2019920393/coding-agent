"""
命令系统

[Workflow]
1. 定义所有内置命令
2. 提供命令查找和过滤函数（对齐 findCommand / hasCommand / getCommand）
3. 支持动态命令注册（技能、插件，后续扩展）
"""

from typing import List, Optional

# 从 base 模块导入命令基础类型
from .base import (
    Command,
    CommandArgumentOption,
    CommandArgumentSpec,
    CommandResult,
    CommandType,
)

# ============================================================================
# 内置命令定义

# 每个命令对象只包含元数据，execute 函数在后续阶段绑定
# ============================================================================

# /help — 显示帮助信息

help_command = Command(
    name="help",
    description="显示可用命令列表",
    aliases=["h", "?"],
)

# /skills — 查看当前可用 skill
skills_command = Command(
    name="skills",
    description="查看或重新加载当前可用 skill",
    argument_hint="[reload|<skill-name>]",
    argument_spec=CommandArgumentSpec(
        kind="text",
        placeholder="留空查看列表，或输入 reload / skill 名称",
    ),
)

# /clear — 清除对话历史

clear_command = Command(
    name="clear",
    description="清除对话历史并释放上下文空间",
    aliases=["reset", "new"],
)

# /compact — 压缩对话历史

compact_command = Command(
    name="compact",
    description="压缩对话历史但保留摘要。可选：/compact [压缩指令]",
    argument_hint="<可选的自定义压缩指令>",
    argument_spec=CommandArgumentSpec(kind="text", placeholder="输入压缩指令"),
)

# /exit — 退出

exit_command = Command(
    name="exit",
    description="退出 Codo",
    aliases=["quit", "q"],
)

# /model — 显示模型信息

model_command = Command(
    name="model",
    description="显示或切换当前模型",
)

# /context — 显示上下文信息

context_command = Command(
    name="context",
    description="显示当前上下文使用情况",
    aliases=["usage"],
)

# /sessions — 显示会话列表

sessions_command = Command(
    name="sessions",
    description="查看或恢复当前工作区的历史会话",
    aliases=["session"],
    argument_hint="<会话 ID 或标题片段>",
    argument_spec=CommandArgumentSpec(kind="text", placeholder="输入会话 ID 或标题片段"),
)

# /memory — 记忆管理

memory_command = Command(
    name="memory",
    description="管理记忆文件（list/view/delete/index）",
    argument_hint="<subcommand>",
    argument_spec=CommandArgumentSpec(
        kind="select",
        placeholder="选择 memory 子命令",
        options=[
            CommandArgumentOption("list", "list", "列出记忆"),
            CommandArgumentOption("view", "view", "查看记忆"),
            CommandArgumentOption("delete", "delete", "删除记忆"),
            CommandArgumentOption("index", "index", "重建索引"),
        ],
        allow_custom=False,
    ),
)

# /mcp-list — 列出 MCP 服务器

mcp_list_command = Command(
    name="mcp-list",
    description="列出已配置的 MCP 服务器",
)

# /mcp-connect — 连接 MCP 服务器

mcp_connect_command = Command(
    name="mcp-connect",
    description="连接到 MCP 服务器",
    argument_hint="<server-name>",
    argument_spec=CommandArgumentSpec(kind="text", placeholder="输入 server-name"),
)

# /mcp-disconnect — 断开 MCP 服务器

mcp_disconnect_command = Command(
    name="mcp-disconnect",
    description="断开 MCP 服务器连接",
    argument_hint="<server-name>",
    argument_spec=CommandArgumentSpec(kind="text", placeholder="输入 server-name"),
)

# /mcp-tools — 列出 MCP 工具

mcp_tools_command = Command(
    name="mcp-tools",
    description="列出 MCP 服务器提供的工具",
    argument_hint="[server-name]",
    argument_spec=CommandArgumentSpec(kind="text", placeholder="可选 server-name"),
)

# /mcp-resources — 列出 MCP 资源

mcp_resources_command = Command(
    name="mcp-resources",
    description="列出 MCP 服务器提供的资源",
    argument_hint="[server-name]",
    argument_spec=CommandArgumentSpec(kind="text", placeholder="可选 server-name"),
)

# /version — 显示版本

version_command = Command(
    name="version",
    description="显示当前版本",
)

# /diff — 显示文件变更

diff_command = Command(
    name="diff",
    description="显示未提交的文件变更",
)

# /export — 导出对话

export_command = Command(
    name="export",
    description="导出当前对话到文件",
    argument_hint="[filename]",
    argument_spec=CommandArgumentSpec(kind="text", placeholder="可选文件名"),
)

# /config — 查看配置

config_command = Command(
    name="config",
    description="查看或修改配置",
)

# /permissions — 切换权限模式
permissions_command = Command(
    name="permissions",
    description="显示或切换当前会话的权限模式",
    aliases=["perm", "p"],
    argument_spec=CommandArgumentSpec(
        kind="select",
        placeholder="show | ask [--strict] | bypass [confirm]",
        options=[
            CommandArgumentOption("show", "show", "显示当前权限模式"),
            CommandArgumentOption("ask", "ask", "后续工具调用恢复为逐次询问"),
            CommandArgumentOption("ask --strict", "ask --strict", "恢复询问并清空本 session 的 allow 规则"),
            CommandArgumentOption("bypass", "bypass", "切换到绕过权限提示模式"),
            CommandArgumentOption("bypass confirm", "bypass confirm", "首次启用 bypass 时显式确认"),
        ],
        allow_custom=True,
    ),
)

focus_command = Command(
    name="focus",
    description="切换侧栏视角到 global / auto / 指定 agent",
    argument_spec=CommandArgumentSpec(
        kind="select",
        placeholder="global | auto | current | <agent-id>",
        options=[
            CommandArgumentOption("global", "global", "锁定到全局 Todo"),
            CommandArgumentOption("auto", "auto", "恢复自动跟随"),
            CommandArgumentOption("current", "current", "切到当前活跃 agent"),
        ],
        allow_custom=True,
    ),
)

# /doctor — 诊断环境

doctor_command = Command(
    name="doctor",
    description="诊断环境问题",
)

# /status — 显示状态

status_command = Command(
    name="status",
    description="显示当前会话状态",
)

# ============================================================================
# 内置命令列表

# ============================================================================

BUILTIN_COMMANDS: List[Command] = [
    help_command,
    skills_command,
    clear_command,
    compact_command,
    exit_command,
    model_command,
    context_command,
    sessions_command,
    memory_command,
    mcp_list_command,
    mcp_connect_command,
    mcp_disconnect_command,
    mcp_tools_command,
    mcp_resources_command,
    version_command,
    diff_command,
    export_command,
    config_command,
    permissions_command,
    focus_command,
    doctor_command,
    status_command,
]

# ============================================================================
# 命令查找函数

# ============================================================================

def find_command(name: str, commands: Optional[List[Command]] = None) -> Optional[Command]:
    """
    根据名称或别名查找命令

    [Workflow]
    1. 去掉可能的 / 前缀并转小写
    2. 遍历命令列表
    3. 匹配命令名称或别名
    4. 检查命令是否启用
    5. 返回匹配的命令或 None

    Args:
        name: 命令名称（不含 / 前缀）
        commands: 命令列表（默认使用 BUILTIN_COMMANDS）

    Returns:
        匹配的命令，或 None
    """
    # 默认使用内置命令列表
    if commands is None:
        commands = BUILTIN_COMMANDS

    # 去掉可能的 / 前缀并统一为小写，确保匹配不区分大小写
    name = name.lstrip("/").lower()

    # 遍历所有命令进行匹配
    for cmd in commands:

        if not cmd.enabled():
            continue
        # 精确匹配命令名称
        if cmd.name == name:
            return cmd
        # 匹配别名列表中的任意一个
        if name in cmd.aliases:
            return cmd

    # 未找到匹配的命令
    return None

def has_command(name: str, commands: Optional[List[Command]] = None) -> bool:
    """
    检查命令是否存在

    [Workflow]
    调用 find_command 并检查返回值是否非 None

    Args:
        name: 命令名称
        commands: 命令列表（默认使用 BUILTIN_COMMANDS）

    Returns:
        命令是否存在
    """
    return find_command(name, commands) is not None

def get_enabled_commands(commands: Optional[List[Command]] = None) -> List[Command]:
    """
    获取所有已启用且非隐藏的命令

    [Workflow]
    1. 遍历命令列表
    2. 过滤掉未启用的命令（enabled() 返回 False）
    3. 过滤掉隐藏的命令（is_hidden 为 True）
    4. 返回已启用的命令列表

    Args:
        commands: 命令列表（默认使用 BUILTIN_COMMANDS）

    Returns:
        已启用且非隐藏的命令列表
    """
    # 默认使用内置命令列表
    if commands is None:
        commands = BUILTIN_COMMANDS

    # 列表推导式过滤：同时满足启用和非隐藏两个条件
    return [cmd for cmd in commands if cmd.enabled() and not cmd.is_hidden]
