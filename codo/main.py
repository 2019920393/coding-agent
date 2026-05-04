"""
Codo 完整 CLI 主逻辑

这个模块负责：
1. 使用 click 框架解析命令行参数
2. 启动交互式 REPL 或交互式单次命令
3. 初始化配置、认证、工具等

[Workflow]
1. 初始化环境（配置、认证、工具注册）
2. 解析命令行参数（click）
3. 根据参数启动交互式 REPL 或交互式单次命令
"""

import asyncio  # 用于异步执行
import os  # 用于环境变量和文件系统操作
import sys  # 用于命令行参数和退出
from pathlib import Path
from typing import Optional, List, Dict, Any  # 用于类型注解

import click  # 命令行参数解析框架
from dotenv import load_dotenv  # 用于加载 .env 文件

from codo.cli.tui import TextualChatApp, UIBridge
from codo.query_engine import QueryEngine  # 查询引擎
from codo.utils.config import ensure_user_dirs  # 确保用户目录存在
from codo.session.query import validate_uuid  # 会话查询
from codo.session.storage import (
    SessionStorage as RuntimeSessionStorage,
    get_sessions_dir as get_runtime_sessions_dir,
)

# 加载 .env 文件中的环境变量（如 ANTHROPIC_API_KEY）
load_dotenv()

# 版本号（应该从 __init__.py 或 pyproject.toml 读取）
VERSION = "0.1.0"

def _list_runtime_sessions(cwd: str) -> List[Dict[str, Any]]:
    """
    列出指定工作目录下所有可用的运行时会话。

    [Workflow]
    1. 定位会话目录（~/.codo/sessions/<cwd_hash>/）
    2. 遍历所有 .jsonl 文件，跳过事件日志文件（*.events.jsonl）
    3. 对每个文件创建 SessionStorage 并读取会话元信息
    4. 过滤掉不存在的会话，按修改时间倒序排列

    参数:
        cwd: 当前工作目录路径

    返回:
        # 示例：
        [
            {
                "session_id": "a1b2c3d4-...",
                "exists": True,
                "modified": "2024-01-15T10:30:00",
                "message_count": 42,
                "first_prompt": "帮我写一个排序算法",
                "user_title": None,
                "ai_title": "排序算法实现讨论",
            },
            ...
        ]
    """
    directory = Path(get_runtime_sessions_dir(cwd))
    if not directory.exists():
        return []

    sessions: List[Dict[str, Any]] = []
    for session_file in directory.glob("*.jsonl"):
        # 跳过事件日志文件，只处理主会话文件
        if session_file.name.endswith(".events.jsonl"):
            continue
        session_id = session_file.stem  # 文件名（不含扩展名）即为 session_id
        storage = RuntimeSessionStorage(session_id, cwd)
        info = storage.get_session_info()
        # 只保留实际存在的会话（防止空文件或损坏文件干扰）
        if info.get("exists"):
            sessions.append(info)

    # 按修改时间倒序排列，最近的会话排在最前面
    sessions.sort(key=lambda item: item.get("modified") or "", reverse=True)
    return sessions

def _runtime_session_title(info: Dict[str, Any]) -> str:
    """
    从会话元信息中提取可读标题。

    优先级：user_title > ai_title > first_prompt > 空字符串

    参数:
        info: 会话元信息字典，结构如：
            {
                "session_id": "a1b2c3d4-...",
                "user_title": None,
                "ai_title": "排序算法实现讨论",
                "first_prompt": "帮我写一个排序算法",
            }

    返回:
        str: 会话标题，如 "排序算法实现讨论"，若无任何标题则返回 ""
    """
    return str(
        info.get("user_title")
        or info.get("ai_title")
        or info.get("first_prompt")
        or ""
    ).strip()

def _search_runtime_sessions(query: str, cwd: str, *, exact: bool = False) -> List[Dict[str, Any]]:
    """
    按关键词搜索会话列表。

    [Workflow]
    1. 规范化搜索词（去空格、转小写）
    2. 若搜索词为空，直接返回全部会话
    3. 对每个会话构建可搜索字段（session_id、标题、首条 prompt）
    4. 根据 exact 参数决定精确匹配还是模糊包含匹配
    5. 返回命中的会话列表

    参数:
        query: 搜索关键词（支持 session_id、标题、首条 prompt）
        cwd: 当前工作目录
        exact: True 表示精确匹配，False 表示模糊包含匹配

    返回:
        # 示例（模糊搜索 "排序"）：
        [
            {
                "session_id": "a1b2c3d4-...",
                "ai_title": "排序算法实现讨论",
                "first_prompt": "帮我写一个排序算法",
                ...
            }
        ]
    """
    normalized = str(query or "").strip().lower()  # 统一转小写，便于大小写不敏感匹配
    if not normalized:
        # 搜索词为空时返回全部会话，等同于 list
        return _list_runtime_sessions(cwd)

    matches: List[Dict[str, Any]] = []
    for info in _list_runtime_sessions(cwd):
        # 构建可搜索字段列表：session_id、标题、首条 prompt
        searchable = [
            str(info.get("session_id", "") or "").strip(),
            _runtime_session_title(info),
            str(info.get("first_prompt", "") or "").strip(),
        ]
        lowered = [item.lower() for item in searchable if item]  # 过滤空字符串并转小写
        if exact:
            # 精确匹配：任意字段完全等于搜索词
            if any(item == normalized for item in lowered):
                matches.append(info)
        else:
            # 模糊匹配：任意字段包含搜索词
            if any(normalized in item for item in lowered):
                matches.append(info)
    return matches

def _print_error(message: str) -> None:
    """输出红色错误信息到 stderr。"""
    click.secho(message, fg="red", err=True)

def _print_warning(message: str) -> None:
    """输出黄色警告或中断信息到 stderr。"""
    click.secho(message, fg="yellow", err=True)

def _print_dim(message: str) -> None:
    """输出灰色弱提示信息到 stdout。"""
    click.secho(message, fg="bright_black")

def _print_traceback(message: str) -> None:
    """输出调试堆栈信息到 stderr。"""
    click.echo(message, err=True)

def main() -> None:
    """
    完整 CLI 主函数，负责命令解析和执行。

    [Workflow]
    1. 使用 click 解析命令行参数
    2. 根据参数执行相应操作（REPL 或单次命令）

    """
    # 调用 click 命令处理器
    # click 会自动解析 sys.argv 并调用相应的命令处理函数
    cli()

@click.command()
@click.argument("prompt", required=False)  # 可选的 prompt 参数
@click.option(
    "--cwd",
    type=click.Path(exists=True),
    help="Working directory"
)  # 工作目录选项
@click.option(
    "--verbose",
    is_flag=True,
    help="Verbose output"
)  # 详细输出选项
@click.option(
    "-c", "--continue",
    "continue_session",
    is_flag=True,
    help="Continue the most recent conversation"
)  # 继续最近的会话
@click.option(
    "-r", "--resume",
    type=str,
    default=None,
    help="Resume a conversation by session ID or title"
)  # 恢复指定会话
@click.option(
    "--model",
    type=str,
    help="Model for the current session (e.g., 'sonnet', 'opus')"
)  # 模型选择
@click.option(
    "--thinking",
    type=int,
    default=None,
    help="启用 Extended Thinking，指定 budget tokens（如 10000）"
)  # Extended Thinking 支持
def cli(
    prompt: Optional[str] = None,
    cwd: Optional[str] = None,
    verbose: bool = False,
    continue_session: bool = False,
    resume: Optional[str] = None,
    model: Optional[str] = None,
    thinking: Optional[int] = None,
):
    """
    Codo - Personal coding agent

    启动交互式会话（默认）。

    Examples:
        codo                           # 启动交互式 REPL
        codo "列出当前目录的文件"        # 单次命令（交互式）
        codo --continue                # 继续最近的会话
        codo --resume <session-id>     # 恢复指定会话
    """

    # ============================================================
    # 步骤 1: 初始化环境
    # ============================================================

    # 确保用户目录存在（~/.codo/）
    ensure_user_dirs()

    # 检查 API key 是否设置
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        # 如果没有设置 API key，打印错误信息并退出
        _print_error("Error: ANTHROPIC_API_KEY not set")
        click.echo("Please set it in .env file or environment variable", err=True)
        sys.exit(1)

    # 读取可选的 base_url
    base_url = os.getenv("ANTHROPIC_BASE_URL")

    # 设置工作目录（如果指定）
    if cwd:
        os.chdir(cwd)  # 切换到指定目录

    # Extended Thinking 配置
    thinking_config = None
    if thinking:
        thinking_config = {"type": "enabled", "budget_tokens": thinking}

    # ============================================================
    # 步骤 2: 根据参数决定执行模式
    # ============================================================

    # 模式 1: 继续最近的会话（-c/--continue）
    if continue_session:

        _run_async(run_continue_session(api_key, verbose, model))
        return

    # 模式 2: 恢复指定会话（-r/--resume）
    if resume is not None:

        _run_async(run_resume_session(resume, api_key, verbose, model))
        return

    # 模式 3: 交互式模式（默认）
    if prompt:
        # 有 prompt 参数：执行单次命令（交互式）
        _run_async(run_single_prompt(prompt, api_key, verbose, model, base_url, thinking_config))
    else:
        # 无 prompt 参数：启动交互式 REPL

        _run_async(run_repl(api_key, verbose, model, base_url, thinking_config))

def _run_async(coro):
    """
    在新的事件循环中运行异步协程（同步入口）。

    用于将 async 函数桥接到 click 的同步命令处理器中。
    直接使用 asyncio.run() 创建新的事件循环，不复用已有循环。

    参数:
        coro: 待执行的异步协程对象，如 run_repl(...)
    """
    asyncio.run(coro)

async def run_single_prompt(
    prompt: str,
    api_key: str,
    verbose: bool = False,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    thinking_config: Optional[Dict[str, Any]] = None,
) -> None:
    """
    交互式单次命令模式入口。

    [Workflow]
    1. 当前 UI 层已移除
    2. 统一复用 run_repl 的占位逻辑
    3. 等待新的 UI 方案重新接入
    """
    try:
        await run_repl(
            api_key=api_key,
            verbose=verbose,
            model=model,
            base_url=base_url,
            thinking_config=thinking_config,
            initial_prompt=prompt,
        )
    except KeyboardInterrupt:
        _print_warning("\nInterrupted")
    except Exception as e:
        _print_error(f"Error: {e}")
        if verbose:
            import traceback
            _print_traceback(traceback.format_exc())
        sys.exit(1)

async def run_continue_session(
    api_key: str,
    verbose: bool = False,
    model: Optional[str] = None,
) -> None:
    """
    继续最近的会话

    [Workflow]
    1. 获取当前项目目录
    2. 加载最近的会话（session_id = None）
    3. 验证会话数据
    4. 恢复消息历史
    5. 启动 REPL 并传入恢复的消息
    """
    cwd = os.getcwd()

    _print_dim("Loading most recent session...")
    sessions = _list_runtime_sessions(cwd)
    if not sessions:
        _print_error("Error: No recent session found or session is empty")
        sys.exit(1)

    await run_repl(
        api_key=api_key,
        verbose=verbose,
        model=model,
        session_id=str(sessions[0].get("session_id", "") or ""),
    )

async def run_resume_session(
    resume_value: Optional[str],
    api_key: str,
    verbose: bool = False,
    model: Optional[str] = None,
) -> None:
    """
    恢复指定会话

    [Workflow]
    1. 获取当前项目目录
    2. 如果 resume_value 为 None，显示交互式选择器
    3. 如果 resume_value 是 UUID，直接加载会话
    4. 如果 resume_value 是标题，按标题精确匹配
    5. 如果匹配到一个会话，加载并恢复
    6. 如果匹配多个，显示交互式选择器并传入搜索词
    """
    cwd = os.getcwd()

    if resume_value is None:
        await run_repl(
            api_key=api_key,
            verbose=verbose,
            model=model,
        )
        return

    if validate_uuid(resume_value):
        exact_id_match = next(
            (
                item
                for item in _list_runtime_sessions(cwd)
                if str(item.get("session_id", "") or "").strip().lower() == resume_value.lower()
            ),
            None,
        )
        if exact_id_match is None:
            await run_repl(
                api_key=api_key,
                verbose=verbose,
                model=model,
                initial_session_query=resume_value,
            )
            return
        await run_repl(
            api_key=api_key,
            verbose=verbose,
            model=model,
            session_id=resume_value,
        )
        return

    matches = _search_runtime_sessions(resume_value, cwd, exact=True)
    if len(matches) == 1:
        await run_repl(
            api_key=api_key,
            verbose=verbose,
            model=model,
            session_id=str(matches[0].get("session_id", "") or ""),
        )
        return

    await run_repl(
        api_key=api_key,
        verbose=verbose,
        model=model,
        initial_session_query=resume_value,
    )

async def run_repl_with_history(
    api_key: str,
    initial_messages: List[Dict[str, Any]],
    verbose: bool = False,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
) -> None:
    """
    兼容旧接口：直接启动新的 Textual UI，并传入历史消息。

    参数:
        api_key: Anthropic API 密钥
        initial_messages: 预加载的历史消息列表，格式如：
            [
                {"role": "user", "content": "你好", "uuid": "msg_001"},
                {"role": "assistant", "content": [{"type": "text", "text": "你好！"}], "uuid": "msg_002"},
            ]
        verbose: 是否输出详细日志
        model: 模型名称，如 "claude-opus-4-20250514"
        base_url: 可选的自定义 API base URL
    """
    await launch_textual_app(
        api_key=api_key,
        verbose=verbose,
        model=model,
        base_url=base_url,
        initial_messages=initial_messages,
    )

async def run_repl(
    api_key: str,
    verbose: bool = False,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    thinking_config: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    initial_prompt: Optional[str] = None,
    initial_session_query: str = "",
) -> None:
    """
    交互式 REPL 模式入口。

    [Workflow]
    1. 创建 QueryEngine
    2. 通过 UIBridge 组装 Textual 状态桥接
    3. 启动新的 Textual App
    """
    await launch_textual_app(
        api_key=api_key,
        verbose=verbose,
        model=model,
        base_url=base_url,
        thinking_config=thinking_config,
        session_id=session_id,
        initial_prompt=initial_prompt,
        initial_session_query=initial_session_query,
    )

async def launch_textual_app(
    api_key: str,
    verbose: bool = False,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    thinking_config: Optional[Dict[str, Any]] = None,
    session_id: Optional[str] = None,
    initial_prompt: Optional[str] = None,
    initial_session_query: str = "",
    initial_messages: Optional[List[Dict[str, Any]]] = None,
) -> None:
    """
    创建 QueryEngine 和 UIBridge，然后运行 Textual UI 应用。

    [Workflow]
    1. 实例化 QueryEngine（携带 API 客户端、模型、会话配置）
    2. 若指定了 session_id 且无预加载消息，则从磁盘恢复会话历史
    3. 创建 UIBridge 将引擎状态桥接到 Textual UI
    4. 创建 TextualChatApp 并异步运行

    参数:
        api_key: Anthropic API 密钥
        verbose: 是否输出详细调试日志
        model: 模型名称，默认 "claude-opus-4-20250514"
        base_url: 可选的自定义 API base URL
        thinking_config: Extended Thinking 配置，如：
            {"type": "enabled", "budget_tokens": 10000}
            None 表示禁用
        session_id: 要恢复的会话 ID（UUID 字符串），None 表示新建会话
        initial_prompt: 启动后自动发送的首条消息
        initial_session_query: 会话选择器的初始搜索词
        initial_messages: 预加载的历史消息列表（优先级高于 session_id 恢复）
    """
    engine = QueryEngine(
        api_key=api_key,
        cwd=os.getcwd(),
        verbose=verbose,
        model=model or "claude-opus-4-20250514",
        session_id=session_id,
        enable_persistence=True,
        initial_messages=initial_messages,
        base_url=base_url,
        thinking_config=thinking_config,
    )

    # 若指定了 session_id 且没有预加载消息，则从磁盘恢复历史
    if session_id and not initial_messages:
        engine.restore_session()

    # 创建 UIBridge，将引擎事件流桥接到 Textual 响应式 UI
    bridge = UIBridge(engine)
    app = TextualChatApp(
        bridge=bridge,
        initial_prompt=initial_prompt,
        initial_session_query=initial_session_query,
    )
    # 异步运行 Textual 应用，阻塞直到用户退出
    await app.run_async()

# 如果直接运行此文件（不太可能，因为应该通过 __main__.py 启动）
if __name__ == "__main__":
    main()
