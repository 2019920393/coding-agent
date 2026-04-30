"""
上下文注入服务

提供环境上下文注入功能：Git 状态、CODO.md 读取、日期信息等。

参考：src/context.ts
- getGitStatus(): 并行执行 5 个 git 命令，返回格式化字符串
- getSystemContext(): 返回 gitStatus 和 cacheBreaker
- getUserContext(): 返回 codoMd 和 currentDate
- 使用 memoize 缓存，整个会话期间只执行一次
"""

import os
import subprocess
import asyncio
from typing import Optional, Dict
from datetime import datetime
from pathlib import Path
from functools import lru_cache
import time

from codo.utils.diagnostics import log_info, log_error
from codo.services.prompt.codomd import get_codo_mds

MAX_STATUS_CHARS = 2000

async def _run_git_command(cmd: list, cwd: str) -> str:
    """
    异步执行单个 git 命令

    Args:
        cmd: git 命令列表
        cwd: 工作目录

    Returns:
        命令输出，失败返回空字符串
    """
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await process.communicate()
        if process.returncode == 0:
            return stdout.decode('utf-8').strip()
        return ""
    except Exception:
        return ""

async def _get_default_branch(cwd: str) -> str:
    """
    获取默认主分支名称（带完整回退逻辑）

    策略：
    1. 读取 refs/remotes/origin/HEAD 符号引用
    2. 验证该分支是否存在
    3. 回退检查 main 和 master 是否存在
    4. 最终回退到 'main'

    Args:
        cwd: 工作目录

    Returns:
        主分支名称
    """
    # 策略 1: 读取 symbolic-ref
    symref = await _run_git_command(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        cwd
    )
    if symref:
        # 提取分支名（refs/remotes/origin/main -> main）
        if symref.startswith("refs/remotes/origin/"):
            branch = symref[len("refs/remotes/origin/"):]
            # 验证分支是否存在
            ref_exists = await _run_git_command(
                ["git", "show-ref", "--verify", f"refs/remotes/origin/{branch}"],
                cwd
            )
            if ref_exists:
                return branch

    # 策略 2: 检查 main 或 master 是否存在
    for candidate in ["main", "master"]:
        ref_exists = await _run_git_command(
            ["git", "show-ref", "--verify", f"refs/remotes/origin/{candidate}"],
            cwd
        )
        if ref_exists:
            return candidate

    # 策略 3: 最终回退
    return "main"

async def _get_git_status_async(cwd: str) -> Optional[str]:
    """
    异步获取 Git 状态（会话开始时的快照）

    并行执行 5 个 git 命令：
    1. git rev-parse --abbrev-ref HEAD  # 当前分支
    2. 主分支获取（带回退逻辑）
    3. git --no-optional-locks status --short  # 状态
    4. git --no-optional-locks log --oneline -n 5  # 最近 5 条提交
    5. git config user.name  # 用户名

    Args:
        cwd: 工作目录

    Returns:
        格式化的 Git 状态字符串，如果不是 Git 仓库则返回 None
    """
    start_time = time.time()
    log_info('git_status_started')

    # 检查环境变量：测试模式跳过
    if os.getenv('NODE_ENV') == 'test':
        log_info('git_status_skipped_test_mode', {
            'duration_ms': int((time.time() - start_time) * 1000)
        })
        return None

    # 检查是否是 Git 仓库
    is_git_start = time.time()
    is_git = await _run_git_command(["git", "rev-parse", "--git-dir"], cwd)
    log_info('git_is_git_check_completed', {
        'duration_ms': int((time.time() - is_git_start) * 1000),
        'is_git': bool(is_git)
    })

    if not is_git:
        log_info('git_status_skipped_not_git', {
            'duration_ms': int((time.time() - start_time) * 1000)
        })
        return None

    try:
        # 并行执行 5 个 git 命令
        git_cmds_start = time.time()
        branch, main_branch, status, log_output, user_name = await asyncio.gather(
            _run_git_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd),
            _get_default_branch(cwd),
            _run_git_command(["git", "--no-optional-locks", "status", "--short"], cwd),
            _run_git_command(["git", "--no-optional-locks", "log", "--oneline", "-n", "5"], cwd),
            _run_git_command(["git", "config", "user.name"], cwd),
            return_exceptions=True
        )

        # 处理可能的异常结果
        branch = branch if isinstance(branch, str) else ""
        main_branch = main_branch if isinstance(main_branch, str) else "main"
        status = status if isinstance(status, str) else ""
        log_output = log_output if isinstance(log_output, str) else ""
        user_name = user_name if isinstance(user_name, str) else ""

        log_info('git_commands_completed', {
            'duration_ms': int((time.time() - git_cmds_start) * 1000),
            'status_length': len(status)
        })

        # 截断 status 到 2000 字符
        truncated = len(status) > MAX_STATUS_CHARS
        truncated_status = status
        if truncated:
            truncated_status = (
                status[:MAX_STATUS_CHARS] +
                "\n... (truncated because it exceeds 2k characters. If you need more information, run \"git status\" using BashTool)"
            )

        log_info('git_status_completed', {
            'duration_ms': int((time.time() - start_time) * 1000),
            'truncated': truncated
        })

        parts = [
            "This is the git status at the start of the conversation. Note that this status is a snapshot in time, and will not update during the conversation.",
            f"Current branch: {branch}",
            f"Main branch (you will usually use this for PRs): {main_branch}",
        ]

        if user_name:
            parts.append(f"Git user: {user_name}")

        parts.append(f"Status:\n{truncated_status or '(clean)'}")
        parts.append(f"Recent commits:\n{log_output}")

        return "\n\n".join(parts)

    except Exception as e:
        log_error('git_status_failed', {
            'duration_ms': int((time.time() - start_time) * 1000),
            'error': str(e)
        })
        return None

# 缓存包装器（同步接口）
_git_status_cache: Dict[str, Optional[str]] = {}

def get_git_status(cwd: str) -> Optional[str]:
    """
    获取 Git 状态（同步接口，内部使用异步实现）

    Args:
        cwd: 工作目录

    Returns:
        格式化的 Git 状态字符串，如果不是 Git 仓库则返回 None
    """
    if cwd in _git_status_cache:
        return _git_status_cache[cwd]

    # 运行异步函数
    try:
        # 尝试获取当前运行的事件循环
        try:
            loop = asyncio.get_running_loop()
            # 如果事件循环已在运行，在线程池中执行
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                result = executor.submit(
                    lambda: asyncio.run(_get_git_status_async(cwd))
                ).result()
        except RuntimeError:
            # 没有运行中的事件循环，直接使用 asyncio.run
            result = asyncio.run(_get_git_status_async(cwd))
    except Exception as e:
        log_error('git_status_sync_wrapper_failed', {'error': str(e)})
        result = None

    _git_status_cache[cwd] = result
    return result

# 系统上下文缓存
_system_context_cache: Dict[str, Dict[str, str]] = {}

def get_system_context(cwd: str) -> Dict[str, str]:
    """
    获取系统上下文（会话期间缓存复用）

    包含：
    - gitStatus: Git 状态快照
    - cacheBreaker: 缓存破坏器（如果启用）

    Args:
        cwd: 工作目录

    Returns:
        系统上下文字典
    """
    if cwd in _system_context_cache:
        return _system_context_cache[cwd]

    start_time = time.time()
    log_info('system_context_started')

    context = {}

    # 检查环境变量：CODO_REMOTE 跳过 git 状态
    should_skip_git = os.getenv('CODO_REMOTE') == 'true'

    # 获取 Git 状态
    git_status = None
    if not should_skip_git:
        git_status = get_git_status(cwd)
        if git_status:
            context["gitStatus"] = git_status

    log_info('system_context_completed', {
        'duration_ms': int((time.time() - start_time) * 1000),
        'has_git_status': git_status is not None
    })

    _system_context_cache[cwd] = context
    return context

# 用户上下文缓存
_user_context_cache: Dict[str, Dict[str, str]] = {}

def get_user_context(cwd: str) -> Dict[str, str]:
    """
    获取用户上下文（会话期间缓存复用）

    包含：
    - codoMd: CODO.md 内容（多位置支持）
    - currentDate: 当前日期
    - autoMemory: 自动记忆系统内容

    Args:
        cwd: 工作目录

    Returns:
        用户上下文字典
    """
    if cwd in _user_context_cache:
        return _user_context_cache[cwd]

    start_time = time.time()
    log_info('user_context_started')

    context = {}

    # 检查环境变量：CODO_DISABLE_CODO_MDS 禁用 CODO.md
    should_disable_codomd = os.getenv('CODO_DISABLE_CODO_MDS') == 'true'

    # 读取 CODO.md（多位置支持）
    codomd_length = 0
    if not should_disable_codomd:
        codo_md_content = get_codo_mds(cwd)
        if codo_md_content:
            context["codoMd"] = codo_md_content
            codomd_length = len(codo_md_content)

    # 添加当前日期（支持环境变量覆盖）
    override_date = os.getenv('CODO_OVERRIDE_DATE')
    if override_date:
        context["currentDate"] = f"Today's date is {override_date}."
    else:
        context["currentDate"] = f"Today's date is {datetime.now().strftime('%Y-%m-%d')}."

    # 加载自动记忆系统
    try:
        from codo.services.memory.paths import ensure_memory_dir
        from codo.services.memory.scan import load_memory_index
        memory_dir = str(ensure_memory_dir(cwd))
        index_content = load_memory_index(cwd)
        memory_context = None
        if index_content:
            memory_context = (
                f"""# auto memory

You have a persistent, file-based memory system at `{memory_dir}`. This directory already exists and can be updated directly.

Use it to retain durable collaboration context across sessions: user preferences, confirmed project facts, long-running task context, references, and high-signal feedback.

## Types of memory

Store durable information in topic-based markdown files with frontmatter and keep `MEMORY.md` as the concise index.

## How to save memories

1. Write or update a dedicated markdown file with frontmatter fields `name`, `description`, and `type`.
2. Add or update a matching entry in `MEMORY.md`.

{index_content}
"""
            )
        if memory_context:
            context["autoMemory"] = memory_context
            log_info('auto_memory_loaded', {
                'memory_length': len(memory_context)
            })
    except Exception as e:
        log_error('auto_memory_load_failed', {'error': str(e)})

    log_info('user_context_completed', {
        'duration_ms': int((time.time() - start_time) * 1000),
        'codomd_length': codomd_length,
        'codomd_disabled': should_disable_codomd,
        'has_auto_memory': 'autoMemory' in context
    })

    _user_context_cache[cwd] = context
    return context

def clear_context_cache():
    """
    清空上下文缓存

    用于 system prompt 注入内容变化时立刻清空缓存
    """
    _git_status_cache.clear()
    _system_context_cache.clear()
    _user_context_cache.clear()

# 兼容层：为旧测试提供 ContextProvider 类
class ContextProvider:
    """
    上下文提供者（兼容层）

    为旧的测试代码提供兼容接口
    """

    def __init__(self, cwd: str):
        self.cwd = cwd

    def is_git_repository(self) -> bool:
        """检查是否为 Git 仓库"""
        git_status = get_git_status(self.cwd)
        return git_status is not None

    def get_git_status(self) -> Optional[str]:
        """获取 Git 状态"""
        return get_git_status(self.cwd)

    def read_codo_md(self) -> Optional[str]:
        """读取 CODO.md"""
        from codo.services.prompt.codomd import get_codo_mds
        return get_codo_mds(self.cwd)

    def get_current_date(self) -> str:
        """获取当前日期"""
        override_date = os.getenv('CODO_OVERRIDE_DATE')
        if override_date:
            return override_date
        return datetime.now().strftime('%Y-%m-%d')

    def get_user_context(self) -> str:
        """获取用户上下文（格式化字符串）"""
        context = get_user_context(self.cwd)
        parts = []

        if 'codoMd' in context:
            parts.append(context['codoMd'])

        if 'currentDate' in context:
            parts.append(f"Current Date: {self.get_current_date()}")

        return '\n\n'.join(parts) if parts else ''

def get_context_for_cwd(cwd: str) -> Dict[str, str]:
    """
    获取指定工作目录的完整上下文（兼容函数）

    Args:
        cwd: 工作目录

    Returns:
        包含系统和用户上下文的字典
    """
    result = {}
    result.update(get_system_context(cwd))
    result.update(get_user_context(cwd))
    return result
