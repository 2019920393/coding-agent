"""
命令系统基类

[Workflow]
定义命令的基础类型和接口，供所有 slash 命令实现。

保留 local 和 prompt 两种类型。
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

class CommandType(str, Enum):
    """
    命令类型枚举

    - prompt: 生成 prompt 发送给模型（技能系统）

    [Workflow]
    继承 str 使枚举值可直接用于字符串比较
    """
    LOCAL = "local"      # 本地执行命令
    PROMPT = "prompt"    # 提示词命令（技能系统）

@dataclass
class CommandResult:
    """
    命令执行结果

    [Workflow]
    封装命令执行后的返回值，包含类型和内容。
    - text: 显示文本（对齐 { type: 'text', value: string }）
    - compact: 压缩结果（对齐 { type: 'compact', compactionResult, displayText }）
    - skip: 跳过显示（对齐 { type: 'skip' }）
    """
    # 结果类型：text（显示文本）、compact（压缩结果）、skip（跳过显示）
    type: str = "text"
    # 结果内容
    value: str = ""
    # 额外数据（如 compact 结果中的 compactionResult）
    data: Optional[Dict[str, Any]] = None

@dataclass
class CommandArgumentOption:
    """命令参数候选项。"""

    value: str
    label: str
    description: str = ""

@dataclass
class CommandArgumentSpec:
    """结构化命令参数定义，供 UI 选择器和 ghost text 使用。"""

    kind: str = "text"
    placeholder: str = ""
    options: List[CommandArgumentOption] = field(default_factory=list)
    allow_custom: bool = True

@dataclass
class Command:
    """
    命令定义

    [Workflow]
    定义一个 slash 命令的所有元数据和执行逻辑。
    LocalCommand 添加 load() 懒加载，PromptCommand 添加 getPromptForCommand()。
    Python 版本将两者合并为单一 Command 数据类，通过 type 字段区分。
    """

    name: str

    description: str

    type: CommandType = CommandType.LOCAL

    aliases: List[str] = field(default_factory=list)

    argument_hint: str = ""
    # 结构化参数定义，供 Textual UI 生成选择器/占位提示
    argument_spec: Optional[CommandArgumentSpec] = None

    is_hidden: bool = False

    is_enabled: Optional[Callable[[], bool]] = None

    source: str = "builtin"

    # 签名：async def execute(args: str, context: dict) -> CommandResult
    execute: Optional[Callable] = None

    # 签名：async def get_prompt(args: str, context: dict) -> str
    get_prompt: Optional[Callable] = None

    def enabled(self) -> bool:
        """
        检查命令是否启用

        [Workflow]
        1. 如果有 is_enabled 回调，调用它获取动态启用状态
        2. 否则默认启用（返回 True）
        """
        # 存在动态启用回调时，调用回调判断
        if self.is_enabled is not None:
            return self.is_enabled()

        return True
