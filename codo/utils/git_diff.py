"""
Git Diff 生成工具模块

提供 Git 风格的 diff 生成功能。

[Workflow]
1. generateGitDiff(): 生成 git diff 格式
2. fetchSingleFileGitDiff(): 从 git 仓库获取文件 diff
"""

import subprocess
from typing import Optional
from dataclasses import dataclass

@dataclass
class GitDiff:
    """Git diff 数据结构"""
    diff: str  # diff 内容
    oldPath: str  # 旧文件路径
    newPath: str  # 新文件路径
    oldMode: Optional[str] = None  # 旧文件模式
    newMode: Optional[str] = None  # 新文件模式
    isNew: bool = False  # 是否为新文件
    isDeleted: bool = False  # 是否为删除文件
    isRenamed: bool = False  # 是否为重命名

def generateGitDiff(
    original: str,
    modified: str,
    filepath: str,
    is_new_file: bool = False
) -> GitDiff:
    """
    生成 git diff 格式

    Args:
        original: 原始内容
        modified: 修改后内容
        filepath: 文件路径
        is_new_file: 是否为新文件

    Returns:
        GitDiff 对象

    Examples:
        >>> original = "line1\\nline2"
        >>> modified = "line1\\nmodified"
        >>> diff = generateGitDiff(original, modified, "test.txt")
        >>> print(diff.diff)
        diff --git a/test.txt b/test.txt
        index 1234567..abcdefg 100644
        --- a/test.txt
        +++ b/test.txt
        @@ -1,2 +1,2 @@
         line1
        -line2
        +modified
    """
    from .diff import generateUnifiedDiff

    # 生成 unified diff
    unified_diff = generateUnifiedDiff(
        original,
        modified,
        fromfile=f'a/{filepath}',
        tofile=f'b/{filepath}'
    )

    # 构建 git diff 头部
    lines = []
    lines.append(f'diff --git a/{filepath} b/{filepath}')

    if is_new_file:
        lines.append('new file mode 100644')
        lines.append('index 0000000..0000000')
    else:
        lines.append('index 0000000..0000000 100644')

    # 添加 unified diff 内容
    lines.append(unified_diff)

    diff_content = '\n'.join(lines)

    return GitDiff(
        diff=diff_content,
        oldPath=filepath,
        newPath=filepath,
        isNew=is_new_file
    )

def fetchSingleFileGitDiff(
    filepath: str,
    cwd: Optional[str] = None
) -> Optional[GitDiff]:
    """
    从 git 仓库获取文件的 diff

    Args:
        filepath: 文件路径
        cwd: 工作目录

    Returns:
        GitDiff 对象，如果不在 git 仓库或无变更则返回 None
    """
    try:
        # 检查是否在 git 仓库中
        subprocess.run(
            ['git', 'rev-parse', '--git-dir'],
            cwd=cwd,
            capture_output=True,
            check=True
        )

        # 获取文件 diff
        result = subprocess.run(
            ['git', 'diff', 'HEAD', '--', filepath],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True
        )

        if not result.stdout.strip():
            # 无变更
            return None

        # 解析 diff 输出
        diff_lines = result.stdout.splitlines()

        # 提取元数据
        is_new = False
        is_deleted = False
        old_mode = None
        new_mode = None

        for line in diff_lines:
            if line.startswith('new file mode'):
                is_new = True
                new_mode = line.split()[-1]
            elif line.startswith('deleted file mode'):
                is_deleted = True
                old_mode = line.split()[-1]

        return GitDiff(
            diff=result.stdout,
            oldPath=filepath,
            newPath=filepath,
            oldMode=old_mode,
            newMode=new_mode,
            isNew=is_new,
            isDeleted=is_deleted
        )

    except (subprocess.CalledProcessError, FileNotFoundError):
        # 不在 git 仓库或 git 不可用
        return None

def isGitRepository(cwd: Optional[str] = None) -> bool:
    """
    检查是否在 git 仓库中

    Args:
        cwd: 工作目录

    Returns:
        是否在 git 仓库中
    """
    try:
        subprocess.run(
            ['git', 'rev-parse', '--git-dir'],
            cwd=cwd,
            capture_output=True,
            check=True
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False
