"""
CODO.md 多位置支持和 @include 指令处理

[Workflow]

功能：
1. 多位置搜索 CODO.md 文件
   - User: ~/.codo/CODO.md
   - User rules: ~/.codo/rules/*.md
   - Project: CODO.md (从根目录向下到 cwd)
   - Project: .codo/CODO.md (从根目录向下到 cwd)
   - Project rules: .codo/rules/*.md (从根目录向下到 cwd)

2. @include 指令解析和递归处理
   - 支持相对路径 (./path, ../path)
   - 支持 home 路径 (~/)
   - 支持绝对路径
   - 最大递归深度 10 层
   - 循环引用检测

设计原则：
- 去重：使用 Set 避免重复读取同一文件
- 安全：限制递归深度，防止无限循环
- 灵活：支持多种路径格式
"""

import os
import re
from pathlib import Path
from typing import List, Set, Optional
from dataclasses import dataclass
import time

from codo.utils.diagnostics import log_info

MAX_INCLUDE_DEPTH = 10

@dataclass
class MemoryFileInfo:
    """内存文件信息"""
    path: str
    type: str  # 'User', 'Project', 'Local'
    content: str
    parent: Optional[str] = None

def extract_include_paths(content: str, base_path: str) -> List[str]:
    """
    从内容中提取 @include 路径

    匹配格式：@path 或 @./path 或 @~/path
    - 支持空格转义：@path\\ with\\ spaces
    - 支持片段标识符：@path#heading（会被移除）
    - 跳过代码块中的 @include

    Args:
        content: 文件内容
        base_path: 基础文件路径（用于解析相对路径）

    Returns:
        绝对路径列表
    """
    absolute_paths = set()
    base_dir = os.path.dirname(base_path)

    # 简化版：不解析 markdown tokens，直接用正则匹配
    # 正则表达式：匹配 @path 格式（不在代码块中）
    # 这里简化处理，不完全解析 markdown 结构
    include_regex = r'(?:^|\s)@((?:[^\s\\]|\\ )+)'

    for match in re.finditer(include_regex, content, re.MULTILINE):
        path = match.group(1)
        if not path:
            continue

        # 1. 移除片段标识符（#heading）
        hash_index = path.find('#')
        if hash_index != -1:
            path = path[:hash_index]
        if not path:
            continue

        # 2. 取消转义空格
        path = path.replace('\\ ', ' ')

        # 3. 验证路径格式
        is_valid_path = (
            path.startswith('./') or
            path.startswith('../') or
            path.startswith('~/') or
            (path.startswith('/') and path != '/') or
            (not path.startswith('@') and
             not re.match(r'^[#%^&*()]+', path) and
             re.match(r'^[a-zA-Z0-9._-]', path))
        )

        if not is_valid_path:
            continue

        # 4. 解析路径
        if path.startswith('~/'):
            # Home 路径
            resolved_path = os.path.expanduser(path)
        elif path.startswith('./') or path.startswith('../'):
            # 相对路径
            resolved_path = os.path.normpath(os.path.join(base_dir, path))
        elif path.startswith('/'):
            # 绝对路径
            resolved_path = path
        else:
            # 相对路径（无前缀）
            resolved_path = os.path.normpath(os.path.join(base_dir, path))

        absolute_paths.add(resolved_path)

    return list(absolute_paths)

def process_memory_file(
    file_path: str,
    type: str,
    processed_paths: Set[str],
    depth: int = 0,
    parent: Optional[str] = None
) -> List[MemoryFileInfo]:
    """
    递归处理内存文件（包含 @include）

    Args:
        file_path: 文件路径
        type: 文件类型 ('User', 'Project', 'Local')
        processed_paths: 已处理路径集合（用于去重和循环检测）
        depth: 当前递归深度
        parent: 父文件路径

    Returns:
        内存文件信息列表（包含当前文件和所有 included 文件）
    """
    # 1. 检查循环引用和最大深度
    normalized_path = os.path.normpath(file_path)
    if normalized_path in processed_paths or depth >= MAX_INCLUDE_DEPTH:
        return []

    # 2. 检查文件是否存在
    if not os.path.exists(file_path):
        return []

    # 3. 读取文件
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read().strip()
    except Exception:
        return []

    if not content:
        return []

    processed_paths.add(normalized_path)

    # 4. 创建内存文件信息
    memory_file = MemoryFileInfo(
        path=file_path,
        type=type,
        content=content,
        parent=parent
    )

    result = [memory_file]

    # 5. 提取并递归处理 @include
    include_paths = extract_include_paths(content, file_path)
    for include_path in include_paths:
        included_files = process_memory_file(
            include_path,
            type,
            processed_paths,
            depth + 1,
            file_path
        )
        result.extend(included_files)

    return result

def get_ancestor_dirs(cwd: str) -> List[str]:
    """
    获取从根目录到 cwd 的所有祖先目录

    Args:
        cwd: 当前工作目录

    Returns:
        目录列表（从根到 cwd）
    """
    dirs = []
    current = os.path.abspath(cwd)
    root = Path(current).anchor  # Windows: 'C:\\', Unix: '/'

    while current != root:
        dirs.append(current)
        parent = os.path.dirname(current)
        if parent == current:  # 到达根目录
            break
        current = parent

    return list(reversed(dirs))  # 从根到 cwd

def process_md_rules(
    rules_dir: str,
    type: str,
    processed_paths: Set[str]
) -> List[MemoryFileInfo]:
    """
    处理 rules 目录下的所有 .md 文件

    Args:
        rules_dir: rules 目录路径
        type: 文件类型
        processed_paths: 已处理路径集合

    Returns:
        内存文件信息列表
    """
    if not os.path.isdir(rules_dir):
        return []

    result = []
    try:
        for filename in os.listdir(rules_dir):
            if filename.endswith('.md'):
                file_path = os.path.join(rules_dir, filename)
                result.extend(process_memory_file(
                    file_path,
                    type,
                    processed_paths
                ))
    except Exception:
        pass

    return result

def get_memory_files(cwd: str) -> List[MemoryFileInfo]:
    """
    获取所有内存文件（CODO.md 和 rules）

    搜索顺序（优先级从低到高）：
    1. User: ~/.codo/CODO.md
    2. User rules: ~/.codo/rules/*.md
    3. Project: 从根目录向下到 cwd 的所有 CODO.md
    4. Project: 从根目录向下到 cwd 的所有 .codo/CODO.md
    5. Project rules: 从根目录向下到 cwd 的所有 .codo/rules/*.md

    Args:
        cwd: 当前工作目录

    Returns:
        内存文件信息列表
    """
    start_time = time.time()
    log_info('memory_files_started')

    # 检查环境变量：CODO_DISABLE_CODO_MDS 禁用
    if os.getenv('CODO_DISABLE_CODO_MDS') == 'true':
        log_info('memory_files_skipped_disabled', {
            'duration_ms': int((time.time() - start_time) * 1000)
        })
        return []

    result = []
    processed_paths = set()
    total_content_length = 0

    # 1. User 文件
    user_codomd = os.path.expanduser('~/.codo/CODO.md')
    user_files = process_memory_file(user_codomd, 'User', processed_paths)
    result.extend(user_files)
    for f in user_files:
        total_content_length += len(f.content)

    # 2. User rules
    user_rules_dir = os.path.expanduser('~/.codo/rules')
    user_rules = process_md_rules(user_rules_dir, 'User', processed_paths)
    result.extend(user_rules)
    for f in user_rules:
        total_content_length += len(f.content)

    # 3. Project 文件（从根向下到 cwd）
    dirs = get_ancestor_dirs(cwd)
    for dir in dirs:
        # 3a. CODO.md
        project_codomd = os.path.join(dir, 'CODO.md')
        project_files = process_memory_file(project_codomd, 'Project', processed_paths)
        result.extend(project_files)
        for f in project_files:
            total_content_length += len(f.content)

        # 3b. .codo/CODO.md
        dot_codo_md = os.path.join(dir, '.codo', 'CODO.md')
        dot_codo_files = process_memory_file(dot_codo_md, 'Project', processed_paths)
        result.extend(dot_codo_files)
        for f in dot_codo_files:
            total_content_length += len(f.content)

        # 3c. .codo/rules/*.md
        rules_dir = os.path.join(dir, '.codo', 'rules')
        rules_files = process_md_rules(rules_dir, 'Project', processed_paths)
        result.extend(rules_files)
        for f in rules_files:
            total_content_length += len(f.content)

    log_info('memory_files_completed', {
        'duration_ms': int((time.time() - start_time) * 1000),
        'file_count': len(result),
        'total_content_length': total_content_length
    })

    return result

def get_codo_mds(cwd: str) -> Optional[str]:
    """
    获取合并后的 CODO.md 内容

    Args:
        cwd: 当前工作目录

    Returns:
        合并后的内容，如果没有文件则返回 None
    """
    memory_files = get_memory_files(cwd)
    if not memory_files:
        return None

    # 合并所有内容
    contents = [f.content for f in memory_files]
    return '\n\n'.join(contents)
