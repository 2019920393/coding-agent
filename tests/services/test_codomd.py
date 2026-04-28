"""
CODO.md 多位置支持和 @include 指令测试
"""

import os
import tempfile
from pathlib import Path

from codo.services.prompt.codomd import (
    extract_include_paths,
    process_memory_file,
    get_memory_files,
    get_codo_mds,
    get_ancestor_dirs
)

def test_extract_include_paths_basic():
    """测试基本的 @include 路径提取"""
    content = """
    Some text
    @./relative/path.md
    More text
    @~/home/path.md
    """
    base_path = "/base/file.md"

    paths = extract_include_paths(content, base_path)

    assert len(paths) == 2
    # 使用 os.path.normpath 处理路径分隔符差异
    assert any('relative' in p and 'path.md' in p for p in paths)
    assert any(p.startswith(os.path.expanduser('~')) for p in paths)

def test_extract_include_paths_with_spaces():
    """测试带空格的路径"""
    content = r"@./path\ with\ spaces.md"
    base_path = "/base/file.md"

    paths = extract_include_paths(content, base_path)

    assert len(paths) == 1
    assert 'path with spaces.md' in paths[0]

def test_extract_include_paths_with_fragment():
    """测试带片段标识符的路径"""
    content = "@./path.md#heading"
    base_path = "/base/file.md"

    paths = extract_include_paths(content, base_path)

    assert len(paths) == 1
    assert paths[0].endswith('path.md')
    assert '#' not in paths[0]

def test_process_memory_file_basic():
    """测试基本的文件处理"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建测试文件
        file_path = os.path.join(tmpdir, 'test.md')
        with open(file_path, 'w') as f:
            f.write('Test content')

        processed = set()
        result = process_memory_file(file_path, 'Project', processed)

        assert len(result) == 1
        assert result[0].content == 'Test content'
        assert result[0].type == 'Project'
        assert file_path in processed

def test_process_memory_file_with_include():
    """测试带 @include 的文件处理"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建主文件
        main_file = os.path.join(tmpdir, 'main.md')
        included_file = os.path.join(tmpdir, 'included.md')

        with open(included_file, 'w') as f:
            f.write('Included content')

        with open(main_file, 'w') as f:
            f.write(f'Main content\n@./included.md')

        processed = set()
        result = process_memory_file(main_file, 'Project', processed)

        assert len(result) == 2
        assert result[0].content == 'Main content\n@./included.md'
        assert result[1].content == 'Included content'

def test_process_memory_file_circular_reference():
    """测试循环引用检测"""
    with tempfile.TemporaryDirectory() as tmpdir:
        file1 = os.path.join(tmpdir, 'file1.md')
        file2 = os.path.join(tmpdir, 'file2.md')

        with open(file1, 'w') as f:
            f.write('@./file2.md')

        with open(file2, 'w') as f:
            f.write('@./file1.md')

        processed = set()
        result = process_memory_file(file1, 'Project', processed)

        # 应该只处理两个文件，不会无限循环
        assert len(result) == 2

def test_process_memory_file_max_depth():
    """测试最大递归深度限制"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建深层嵌套的 include 链
        files = []
        for i in range(15):
            file_path = os.path.join(tmpdir, f'file{i}.md')
            files.append(file_path)

            if i < 14:
                with open(file_path, 'w') as f:
                    f.write(f'Content {i}\n@./file{i+1}.md')
            else:
                with open(file_path, 'w') as f:
                    f.write(f'Content {i}')

        processed = set()
        result = process_memory_file(files[0], 'Project', processed)

        # 应该被限制在最大深度（10 层）
        assert len(result) <= 11  # 0-10 = 11 个文件

def test_get_ancestor_dirs():
    """测试获取祖先目录"""
    if os.name == 'nt':  # Windows
        cwd = r'C:\Users\test\project\subdir'
        dirs = get_ancestor_dirs(cwd)

        assert len(dirs) >= 3
        assert dirs[-1] == os.path.abspath(cwd)
    else:  # Unix
        cwd = '/home/test/project/subdir'
        dirs = get_ancestor_dirs(cwd)

        assert len(dirs) >= 3
        assert dirs[-1] == cwd

def test_get_memory_files_user_location():
    """测试 User 位置的 CODO.md"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建 ~/.codo/CODO.md（对齐 Codo 项目路径）
        codo_dir = os.path.join(tmpdir, '.codo')
        os.makedirs(codo_dir, exist_ok=True)

        user_file = os.path.join(codo_dir, 'CODO.md')
        with open(user_file, 'w') as f:
            f.write('User CODO.md content')

        # 临时修改 home 目录
        original_home = os.environ.get('HOME') or os.environ.get('USERPROFILE')
        os.environ['HOME'] = tmpdir
        os.environ['USERPROFILE'] = tmpdir

        try:
            result = get_memory_files(tmpdir)

            # 应该找到 User 文件
            user_files = [f for f in result if f.type == 'User']
            assert len(user_files) >= 1
            assert any('User CODO.md content' in f.content for f in user_files)

        finally:
            if original_home:
                os.environ['HOME'] = original_home
                os.environ['USERPROFILE'] = original_home

def test_get_memory_files_project_location():
    """测试 Project 位置的 CODO.md"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建项目根目录的 CODO.md
        project_file = os.path.join(tmpdir, 'CODO.md')
        with open(project_file, 'w') as f:
            f.write('Project CODO.md content')

        result = get_memory_files(tmpdir)

        # 应该找到 Project 文件
        project_files = [f for f in result if f.type == 'Project']
        assert len(project_files) >= 1
        assert any('Project CODO.md content' in f.content for f in project_files)

def test_get_memory_files_disabled():
    """测试环境变量禁用 CODO.md"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建 CODO.md
        project_file = os.path.join(tmpdir, 'CODO.md')
        with open(project_file, 'w') as f:
            f.write('Content')

        os.environ['CODO_DISABLE_CODO_MDS'] = 'true'

        try:
            result = get_memory_files(tmpdir)
            assert len(result) == 0

        finally:
            del os.environ['CODO_DISABLE_CODO_MDS']

def test_get_codo_mds():
    """测试获取合并后的 CODO.md 内容"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 创建多个 CODO.md 文件
        project_file = os.path.join(tmpdir, 'CODO.md')
        with open(project_file, 'w') as f:
            f.write('Project content')

        dot_codo_dir = os.path.join(tmpdir, '.codo')
        os.makedirs(dot_codo_dir, exist_ok=True)
        dot_codo_file = os.path.join(dot_codo_dir, 'CODO.md')
        with open(dot_codo_file, 'w') as f:
            f.write('Dot codo content')

        result = get_codo_mds(tmpdir)

        assert result is not None
        assert 'Project content' in result
        assert 'Dot codo content' in result
