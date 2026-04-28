"""
BashTool 类型定义

定义 BashTool 的输入输出 schema。
"""

from pydantic import BaseModel, Field
from typing import Optional

class BashToolInput(BaseModel):
    """BashTool 输入参数"""

    command: str = Field(
        description="要执行的 shell 命令"
    )

    timeout: Optional[int] = Field(
        default=120000,
        description="超时时间（毫秒），默认 120000ms（2分钟），最大 600000ms（10分钟）"
    )

    run_in_background: Optional[bool] = Field(
        default=False,
        description="是否在后台运行。后台运行时不会等待命令完成，稍后会收到完成通知"
    )

    description: Optional[str] = Field(
        default=None,
        description="命令描述，用于显示给用户"
    )

class BashToolOutput(BaseModel):
    """BashTool 输出结果"""

    stdout: str = Field(
        description="标准输出内容"
    )

    stderr: str = Field(
        description="标准错误输出内容"
    )

    exitCode: int = Field(
        description="退出码（0 表示成功）"
    )

    command: str = Field(
        description="执行的命令"
    )

    durationMs: int = Field(
        description="执行耗时（毫秒）"
    )

    timedOut: bool = Field(
        description="是否超时"
    )

    background: bool = Field(
        default=False,
        description="是否在后台运行"
    )

    taskId: Optional[str] = Field(
        default=None,
        description="后台任务 ID（仅后台运行时返回）"
    )

    status: Optional[str] = Field(
        default=None,
        description="后台任务状态（仅后台运行时返回）"
    )

class BashToolProgress(BaseModel):
    """BashTool 进度数据"""

    type: str = Field(
        description="进度类型：'stdout' | 'stderr' | 'status'"
    )

    data: str = Field(
        description="进度数据内容"
    )
