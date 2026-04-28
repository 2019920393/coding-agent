"""
测试 Plan Mode 功能

验证：
1. Plan 文件管理（创建、读取、保存）
2. Plan slug 生成和缓存
3. EnterPlanMode 工具
4. ExitPlanMode 工具
"""

import asyncio
import importlib
import tempfile
from pathlib import Path
from unittest.mock import patch

from codo.services.plans import (
    generate_word_slug,
    get_plans_directory,
    get_plan_slug,
    set_plan_slug,
    clear_plan_slug,
    get_plan_file_path,
    get_plan,
    save_plan,
    plan_exists,
    _plan_slug_cache,
)
from codo.tools.plan_mode_tools import (
    EnterPlanModeTool,
    ExitPlanModeTool,
)
from codo.tools.plan_mode_tools.utils import get_plans_directory as get_runtime_plans_directory

def test_generate_word_slug():
    """测试 slug 生成"""
    print("\n=== Test 1: Generate Word Slug ===")

    slug = generate_word_slug()

    # 验证格式：{adjective}-{adjective}-{noun}
    parts = slug.split('-')
    assert len(parts) == 3, f"Expected 3 parts, got {len(parts)}"

    # 验证每部分都是字母
    for part in parts:
        assert part.isalpha(), f"Part '{part}' contains non-alphabetic characters"

    print(f"✓ Generated slug: {slug}")

def test_plan_file_management():
    """测试 Plan 文件管理"""
    print("\n=== Test 2: Plan File Management ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        with patch('codo.services.plans.get_user_dir', return_value=tmpdir_path):
            # 清除缓存
            _plan_slug_cache.clear()

            session_id = "test-session-123"

            # 测试 1: 获取 plan slug
            slug = get_plan_slug(session_id)
            assert slug is not None
            assert len(slug) > 0
            print(f"✓ Generated slug for session: {slug}")

            # 测试 2: 缓存验证
            slug2 = get_plan_slug(session_id)
            assert slug == slug2, "Slug should be cached"
            print("✓ Slug caching works")

            # 测试 3: 获取 plan 文件路径
            file_path = get_plan_file_path(session_id)
            assert file_path.name == f"{slug}.md"
            print(f"✓ Plan file path: {file_path}")

            # 测试 4: 保存 plan
            plan_content = "# Implementation Plan\n\n## Step 1\nDo something\n\n## Step 2\nDo something else"
            saved_path = save_plan(session_id, plan_content)
            assert saved_path.exists()
            print("✓ Plan saved successfully")

            # 测试 5: 读取 plan
            read_content = get_plan(session_id)
            assert read_content == plan_content
            print("✓ Plan read successfully")

            # 测试 6: 检查 plan 是否存在
            assert plan_exists(session_id)
            print("✓ Plan exists check works")

            # 测试 7: 清除 slug
            clear_plan_slug(session_id)
            assert session_id not in _plan_slug_cache
            print("✓ Slug cleared from cache")

def test_plan_slug_with_agent():
    """测试带 agent_id 的 plan 文件"""
    print("\n=== Test 3: Plan with Agent ID ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        with patch('codo.services.plans.get_user_dir', return_value=tmpdir_path):
            _plan_slug_cache.clear()

            session_id = "test-session-456"
            agent_id = "agent-001"

            # 获取主会话的 plan 路径
            main_path = get_plan_file_path(session_id)

            # 获取 agent 的 plan 路径
            agent_path = get_plan_file_path(session_id, agent_id)

            # 验证路径不同
            assert main_path != agent_path
            assert agent_id in agent_path.name
            print(f"✓ Main plan: {main_path.name}")
            print(f"✓ Agent plan: {agent_path.name}")

            # 保存不同的 plan
            save_plan(session_id, "Main plan content")
            save_plan(session_id, "Agent plan content", agent_id)

            # 验证内容不同
            main_content = get_plan(session_id)
            agent_content = get_plan(session_id, agent_id)

            assert main_content == "Main plan content"
            assert agent_content == "Agent plan content"
            print("✓ Main and agent plans are independent")

def test_legacy_plan_mode_path_removed():
    """旧的 codo.tools.plan_mode 兼容层应已删除。"""
    print("\n=== Test 4: Legacy Plan Mode Path Removed ===")

    try:
        importlib.import_module("codo.tools.plan_mode")
    except ModuleNotFoundError:
        pass
    else:
        raise AssertionError("codo.tools.plan_mode should not exist anymore")

def test_runtime_plan_mode_uses_codo_directory():
    """runtime plan mode 默认目录应位于 ~/.codo/plans。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch("pathlib.Path.home", return_value=Path(tmpdir)):
            plans_dir = get_runtime_plans_directory()

        assert plans_dir.endswith(str(Path(".codo") / "plans"))
        assert Path(plans_dir).exists()

if __name__ == "__main__":
    print("=" * 60)
    print("Testing Plan Mode Functionality")
    print("=" * 60)

    try:
        test_generate_word_slug()
        test_plan_file_management()
        test_plan_slug_with_agent()
        test_legacy_plan_mode_path_removed()

        print("\n" + "=" * 60)
        print("✅ All Plan Mode tests passed!")
        print("=" * 60)
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
