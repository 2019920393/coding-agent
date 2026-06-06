"""
测试 Bash 工具的进程保护功能

验证 Bash 工具能够正确阻止可能终止 Codo 自身进程的危险命令。
"""

import pytest
from codo.tools.bash_tool.bash_tool import BashTool
from codo.tools.bash_tool.types import BashToolInput


@pytest.mark.asyncio
async def test_block_kill_codo_process():
    """测试阻止终止 Codo 进程的命令"""
    tool = BashTool()

    # 明确包含 Codo 关键词的危险命令
    dangerous_commands = [
        "taskkill /F /IM Codo.exe",
        "taskkill /F /IM electron.exe",
        "kill -9 $(ps aux | grep 'Codo' | awk '{print $2}')",
        "pkill -9 codo",
        "killall Codo",
        "taskkill /F /IM python.exe /FI \"WINDOWTITLE eq ai_bridge*\"",
    ]

    for cmd in dangerous_commands:
        input_data = BashToolInput(command=cmd)
        result = await tool.validate_input(input_data, {})

        assert result.result is False, f"命令应该被阻止: {cmd}"
        assert "CRITICAL" in result.message, f"应该包含严重警告: {cmd}"
        assert "Codo" in result.message, f"应该提到 Codo: {cmd}"


@pytest.mark.asyncio
async def test_warn_but_allow_port_based_kill():
    """测试基于端口的 kill 命令会警告但不阻止（系统提示会指导 AI 正确使用）"""
    tool = BashTool()

    # 基于端口的 kill 命令（可能影响 Codo，但命令本身没有 Codo 关键词）
    # 这些命令会被记录警告，但不会被代码层阻止
    # 系统提示层会指导 AI 先检查进程再决定是否 kill
    port_based_commands = [
        "kill $(lsof -ti:30000)",  # 可能是 AI bridge 端口
        "taskkill /F /PID 12345",  # 特定 PID（可能是 Codo）
    ]

    for cmd in port_based_commands:
        input_data = BashToolInput(command=cmd)
        result = await tool.validate_input(input_data, {})

        # 这些命令通过验证（由系统提示指导 AI 正确使用）
        assert result.result is True, f"命令应该通过（但会被记录）: {cmd}"


@pytest.mark.asyncio
async def test_allow_safe_kill_commands():
    """测试允许安全的进程终止命令"""
    tool = BashTool()

    safe_commands = [
        "taskkill /F /PID 12345",  # 特定 PID（假设不是 Codo）
        "kill -9 12345",
        "taskkill /F /IM notepad.exe",
        "pkill -9 chrome",
        "killall firefox",
    ]

    for cmd in safe_commands:
        input_data = BashToolInput(command=cmd)
        result = await tool.validate_input(input_data, {})

        # 这些命令应该通过验证（但系统提示仍会警告用户）
        assert result.result is True, f"安全命令应该通过: {cmd}"


@pytest.mark.asyncio
async def test_block_electron_with_codo_path():
    """测试阻止终止包含 Codo 路径的 Electron 进程"""
    tool = BashTool()

    # Windows 路径
    cmd = 'taskkill /F /FI "IMAGENAME eq electron.exe" /FI "COMMANDLINE eq *workbench-app*"'
    input_data = BashToolInput(command=cmd)
    result = await tool.validate_input(input_data, {})

    assert result.result is False
    assert "CRITICAL" in result.message


@pytest.mark.asyncio
async def test_block_python_ai_bridge():
    """测试阻止终止 AI bridge Python 进程"""
    tool = BashTool()

    dangerous_commands = [
        "pkill -f ai_bridge.py",
        "kill $(ps aux | grep ai_bridge | awk '{print $2}')",
        "taskkill /F /FI \"COMMANDLINE eq *ai_bridge*\"",
    ]

    for cmd in dangerous_commands:
        input_data = BashToolInput(command=cmd)
        result = await tool.validate_input(input_data, {})

        assert result.result is False, f"命令应该被阻止: {cmd}"
        assert "CRITICAL" in result.message


@pytest.mark.asyncio
async def test_allow_port_check_commands():
    """测试允许端口检查命令（不终止进程）"""
    tool = BashTool()

    safe_commands = [
        "netstat -ano | findstr :3000",
        "lsof -i :3000",
        "ss -tulpn | grep :3000",
        "tasklist /FI \"PID eq 12345\"",
        "ps aux | grep node",
    ]

    for cmd in safe_commands:
        input_data = BashToolInput(command=cmd)
        result = await tool.validate_input(input_data, {})

        assert result.result is True, f"检查命令应该通过: {cmd}"


@pytest.mark.asyncio
async def test_case_insensitive_protection():
    """测试大小写不敏感的保护"""
    tool = BashTool()

    dangerous_commands = [
        "TASKKILL /F /IM CODO.EXE",
        "Kill -9 $(ps aux | grep CODO)",
        "pkill -9 CoDo",
    ]

    for cmd in dangerous_commands:
        input_data = BashToolInput(command=cmd)
        result = await tool.validate_input(input_data, {})

        assert result.result is False, f"命令应该被阻止（大小写不敏感）: {cmd}"


@pytest.mark.asyncio
async def test_helpful_error_message():
    """测试错误消息是否提供有用的指导"""
    tool = BashTool()

    input_data = BashToolInput(command="taskkill /F /IM Codo.exe")
    result = await tool.validate_input(input_data, {})

    assert result.result is False
    # 检查错误消息是否包含有用的指导
    assert "port conflict" in result.message.lower() or "端口" in result.message
    assert "netstat" in result.message.lower() or "lsof" in result.message.lower()
    assert "alternate port" in result.message.lower() or "alternate" in result.message
