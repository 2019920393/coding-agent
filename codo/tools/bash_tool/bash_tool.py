"""
BashTool 实现

执行 shell 命令并返回输出。

[Workflow]
1. 验证输入参数（超时、命令）
2. 检查权限（如果配置了权限系统）
3. 执行命令（异步子进程）
4. 流式传输输出（通过 onProgress 回调）
5. 返回结果（stdout, stderr, exitCode）
"""

import asyncio
import time
import os
from typing import Optional, Callable, Any
from ..base import Tool, ToolUseContext
from ..types import ToolResult, ValidationResult, ToolCallProgress, ToolProgress
from .types import BashToolInput, BashToolOutput, BashToolProgress
from codo.team import get_task_manager
from .prompt import (
    BASH_TOOL_NAME,
    DESCRIPTION,
    get_user_facing_name,
    get_tool_use_summary,
    get_activity_description
)
from .utils import isReadOnlyCommand

class BashTool(Tool[BashToolInput, BashToolOutput, BashToolProgress]):
    """Bash 命令执行工具"""

    def __init__(self):
        self.name = BASH_TOOL_NAME
        self.max_result_size_chars = 30000  # 30K chars - Bash 输出通常较大

    @property
    def input_schema(self) -> type[BashToolInput]:
        """返回输入 schema"""
        return BashToolInput

    @property
    def output_schema(self) -> type[BashToolOutput]:
        """返回输出 schema"""
        return BashToolOutput

    async def description(self, input_data: BashToolInput, options: dict) -> str:
        """返回工具描述"""
        return DESCRIPTION

    async def prompt(self, options: dict) -> str:
        """返回系统提示"""
        return DESCRIPTION

    def map_tool_result_to_tool_result_block_param(self, content: BashToolOutput, tool_use_id: str):
        """将工具结果转换为 API 格式"""
        if content.background and content.taskId:
            return {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": f"Background task started: {content.taskId}",
            }
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": f"stdout: {content.stdout}\nstderr: {content.stderr}\nexit_code: {content.exitCode}"
        }

    def user_facing_name(self) -> str:
        """返回用户可见的工具名称"""
        return get_user_facing_name()

    def get_tool_use_summary(self, input_data: BashToolInput) -> str:
        """返回工具使用摘要"""
        return get_tool_use_summary(input_data.model_dump())

    def get_activity_description(self, input_data: BashToolInput) -> str:
        """返回活动描述"""
        return get_activity_description(input_data.model_dump())

    def is_concurrency_safe(self, input_data: BashToolInput) -> bool:
        """命令执行不保证并发安全"""
        return False

    def is_read_only(self, input_data: BashToolInput) -> bool:
        """检测命令是否为只读操作"""
        return isReadOnlyCommand(input_data.command)

    async def validate_input(self, input_data: BashToolInput, context: ToolUseContext) -> ValidationResult:
        """
        验证输入参数

        检查：
        1. 超时时间是否在合理范围内
        2. 命令是否为空
        3. 权限检查（如果配置）
        """
        # 检查命令是否为空
        if not input_data.command.strip():
            return ValidationResult(
                result=False,
                message='命令不能为空',
            )

        # 检查超时时间
        if input_data.timeout:
            if input_data.timeout < 0:
                return ValidationResult(
                    result=False,
                    message='超时时间不能为负数',
                )
            if input_data.timeout > 600000:  # 10 分钟
                return ValidationResult(
                    result=False,
                    message='超时时间不能超过 10 分钟（600000ms）',
                )

        # TODO: 权限检查（未来实现）
        # 目前默认允许所有命令

        return ValidationResult(
            result=True,
        )

    async def call(
        self,
        input_data: BashToolInput,
        context: ToolUseContext,
        can_use_tool: Callable,
        parent_message: Any,
        on_progress: Optional[Callable[[ToolCallProgress], None]] = None
    ) -> ToolResult[BashToolOutput]:
        """
        执行 shell 命令

        Args:
            input_data: 输入参数
            context: 工具使用上下文
            on_progress: 进度回调

        Returns:
            工具执行结果
        """
        command = input_data.command
        timeout_ms = input_data.timeout or 120000
        timeout_sec = timeout_ms / 1000.0
        run_in_background = input_data.run_in_background or False
        cwd = context.get("cwd", os.getcwd())

        start_time = time.time()

        # 获取 AbortController（如果有）
        options = context.get_options()
        abort_controller = context.get("abort_controller")

        try:
            # 检查是否已中断
            if abort_controller and abort_controller.is_aborted():
                from codo.utils.abort_controller import get_abort_message
                return ToolResult(
                    data=BashToolOutput(
                        stdout='',
                        stderr=get_abort_message(abort_controller.get_reason()),
                        exitCode=130,  # SIGINT 退出码
                        command=command,
                        durationMs=0,
                        timedOut=False,
                        background=False
                    ),
                    error=get_abort_message(abort_controller.get_reason())
                )

            # 如果在后台运行，立即返回
            if run_in_background:
                task_manager = get_task_manager()
                task = task_manager.create_task(
                    agent_id=context.get("agent_id", "bash"),
                    description=input_data.description or self.get_tool_use_summary(input_data),
                    metadata={
                        "kind": "bash",
                        "command": command,
                        "cwd": cwd,
                    },
                )

                async def execute_in_background() -> dict:
                    return await self._run_background_command(
                        command=command,
                        cwd=cwd,
                        timeout_sec=timeout_sec,
                        timeout_ms=timeout_ms,
                    )

                await task_manager.run_task(task, execute_in_background())

                return ToolResult(
                    data=BashToolOutput(
                        stdout='',
                        stderr='',
                        exitCode=0,
                        command=command,
                        durationMs=0,
                        timedOut=False,
                        background=True,
                        taskId=task.task_id,
                        status=task.status.value,
                    )
                )

            # 创建异步子进程
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd
            )

            # 流式读取输出
            stdout_lines = []
            stderr_lines = []

            async def read_stream(stream, lines_list, stream_type):
                """读取流并发送进度"""
                while True:
                    line = await stream.readline()
                    if not line:
                        break

                    line_str = line.decode('utf-8', errors='replace')
                    lines_list.append(line_str)

                    # 发送进度
                    if on_progress:
                        # 从工具上下文中读取当前 tool_use_id，便于后台任务回传到正确的 UI 卡片
                        tool_use_id = context.get("tool_use_id", "")
                        progress = ToolProgress(
                            tool_use_id=tool_use_id,
                            data=BashToolProgress(
                                type=stream_type,
                                data=line_str
                            )
                        )
                        progress_result = on_progress(progress)
                        if asyncio.iscoroutine(progress_result):
                            await progress_result

            # 等待进程完成（带超时和中断检查）
            try:
                # 注册中断回调（如果有 AbortController）
                abort_callback_unregister = None
                if abort_controller:
                    def on_abort(reason):
                        """中断回调：根据原因决定是否杀死进程"""
                        # 'interrupt' 原因：不杀死进程（用户可能想保留长时间运行的进程）
                        # 'abort' 原因：杀死进程
                        if reason == "abort":
                            try:
                                process.kill()
                            except:
                                pass
                        # 'interrupt' 时不杀死进程，让它继续运行

                    abort_callback_unregister = abort_controller.on_abort(on_abort)

                # 并发读取 stdout 和 stderr，并等待进程完成
                await asyncio.wait_for(
                    asyncio.gather(
                        read_stream(process.stdout, stdout_lines, 'stdout'),
                        read_stream(process.stderr, stderr_lines, 'stderr'),
                        process.wait()
                    ),
                    timeout=timeout_sec
                )
                exit_code = process.returncode
                timed_out = False

                # 取消注册中断回调
                if abort_callback_unregister:
                    abort_callback_unregister()

            except asyncio.TimeoutError:
                # 超时，终止进程
                process.kill()
                await process.wait()
                exit_code = -1
                timed_out = True

            # 计算耗时
            duration_ms = int((time.time() - start_time) * 1000)

            # 合并输出
            stdout = ''.join(stdout_lines)
            stderr = ''.join(stderr_lines)

            if timed_out:
                stderr += f'\n[命令超时，已终止（超时时间: {timeout_ms}ms）]'

            return ToolResult(
                data=BashToolOutput(
                    stdout=stdout,
                    stderr=stderr,
                    exitCode=exit_code,
                    command=command,
                    durationMs=duration_ms,
                    timedOut=timed_out,
                    background=False
                )
            )

        except Exception as e:
            # 执行失败
            duration_ms = int((time.time() - start_time) * 1000)

            return ToolResult(
                data=BashToolOutput(
                    stdout='',
                    stderr=f'命令执行失败: {str(e)}',
                    exitCode=-1,
                    command=command,
                    durationMs=duration_ms,
                    timedOut=False,
                    background=False
                ),
                error=str(e)
            )

    async def _run_background_command(
        self,
        command: str,
        cwd: str,
        timeout_sec: float,
        timeout_ms: int,
    ) -> dict:
        """
        在后台执行命令并返回结构化结果。

        后台任务不依赖前台流式回调，而是一次性收集完整输出，
        以便后台任务管理器和 TUI 在完成时统一展示。
        """
        start_time = time.time()
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        timed_out = False
        stdout_bytes = b""
        stderr_bytes = b""

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_sec,
            )
            exit_code = process.returncode if process.returncode is not None else 0
        except asyncio.TimeoutError:
            timed_out = True
            process.kill()
            stdout_bytes, stderr_bytes = await process.communicate()
            exit_code = -1
        except asyncio.CancelledError:
            if process.returncode is None:
                process.kill()
                await process.communicate()
            raise

        duration_ms = int((time.time() - start_time) * 1000)
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

        if timed_out:
            timeout_notice = f"[命令超时，已终止（超时时间: {timeout_ms}ms）]"
            stderr = f"{stderr}\n{timeout_notice}".strip() if stderr else timeout_notice

        return {
            "command": command,
            "cwd": cwd,
            "stdout": stdout,
            "stderr": stderr,
            "exitCode": exit_code,
            "durationMs": duration_ms,
            "timedOut": timed_out,
            "background": True,
            "result": stdout or stderr,
        }

    def get_context_modifier(
        self,
        input_data: BashToolInput,
        result: ToolResult[BashToolOutput],
        context: dict,
    ) -> Optional[Callable[[dict], dict]]:
        """
        获取上下文修改器

        检测 cd 命令并返回新的工作目录。

        Args:
            input_data: 工具输入（字典或 Pydantic 模型）
            result: 工具执行结果
            context: 当前上下文

        Returns:
            修改函数，或 None
        """
        # 只处理成功的命令
        if result.error or not result.data or result.data.exitCode != 0:
            return None

        # 如果 input_data 是字典，转换为 Pydantic 模型
        if isinstance(input_data, dict):
            input_data = BashToolInput(**input_data)

        command = input_data.command.strip()

        # 检测 cd 命令
        if command.startswith('cd '):
            # 提取目标目录
            target_dir = command[3:].strip()

            # 如果是相对路径，基于当前 cwd 解析
            if not os.path.isabs(target_dir):
                current_cwd = context.get('cwd', os.getcwd())
                target_dir = os.path.join(current_cwd, target_dir)

            # 规范化路径
            target_dir = os.path.normpath(target_dir)

            # 返回修改函数
            def modify_context(ctx: dict) -> dict:
                new_ctx = ctx.copy()
                new_ctx['cwd'] = target_dir
                return new_ctx

            return modify_context

        return None

# 创建工具实例
bash_tool = BashTool()
