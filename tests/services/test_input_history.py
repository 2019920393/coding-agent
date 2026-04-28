"""
Test input history functionality
"""

import os
import sys
import tempfile
from pathlib import Path

# 注意：已从根目录移入 Codo_new/tests/services/，不再需要手动添加路径
# 通过 pip install -e . 安装后，codo 包可直接导入

from codo.services.input_history import (
    InputHistory,
    PastedContent,
    format_pasted_text_ref,
    format_image_ref,
    expand_pasted_text_refs
)

def test_basic_history():
    """Test basic history add and retrieve"""
    print("\n=== Test 1: Basic History ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        history_file = Path(tmpdir) / "history.jsonl"

        history = InputHistory(
            project_root="/test/project",
            session_id="session-1",
            history_file=history_file
        )

        # Add some commands
        history.add_to_history("git status")
        history.add_to_history("git commit -m 'test'")
        history.add_to_history("git push")

        # Get history
        entries = history.get_history()

        print(f"Added 3 commands, retrieved {len(entries)} entries")
        assert len(entries) == 3, f"Expected 3 entries, got {len(entries)}"

        # Check order (newest first)
        assert entries[0].display == "git push"
        assert entries[1].display == "git commit -m 'test'"
        assert entries[2].display == "git status"

        print("✓ Commands stored in correct order (newest first)")

def test_session_priority():
    """Test current session entries appear first"""
    print("\n=== Test 2: Session Priority ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        history_file = Path(tmpdir) / "history.jsonl"

        # Session 1: Add some commands
        history1 = InputHistory(
            project_root="/test/project",
            session_id="session-1",
            history_file=history_file
        )
        history1.add_to_history("command from session 1")
        history1.add_to_history("another from session 1")

        # Session 2: Add more commands
        history2 = InputHistory(
            project_root="/test/project",
            session_id="session-2",
            history_file=history_file
        )
        history2.add_to_history("command from session 2")
        history2.add_to_history("another from session 2")

        # Get history from session 2 - should see session 2 entries first
        entries = history2.get_history()

        print(f"Retrieved {len(entries)} entries")
        assert len(entries) == 4

        # First two should be from session 2
        assert entries[0].display == "another from session 2"
        assert entries[1].display == "command from session 2"

        # Next two from session 1
        assert entries[2].display == "another from session 1"
        assert entries[3].display == "command from session 1"

        print("✓ Current session entries appear first")

def test_project_isolation():
    """Test history is isolated by project"""
    print("\n=== Test 3: Project Isolation ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        history_file = Path(tmpdir) / "history.jsonl"

        # Project A
        history_a = InputHistory(
            project_root="/test/project-a",
            session_id="session-1",
            history_file=history_file
        )
        history_a.add_to_history("command for project A")

        # Project B
        history_b = InputHistory(
            project_root="/test/project-b",
            session_id="session-1",
            history_file=history_file
        )
        history_b.add_to_history("command for project B")

        # Get history for project A - should only see project A commands
        entries_a = history_a.get_history()
        assert len(entries_a) == 1
        assert entries_a[0].display == "command for project A"

        # Get history for project B - should only see project B commands
        entries_b = history_b.get_history()
        assert len(entries_b) == 1
        assert entries_b[0].display == "command for project B"

        print("✓ History correctly isolated by project")

def test_search_history():
    """Test history search (Ctrl+R)"""
    print("\n=== Test 4: Search History ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        history_file = Path(tmpdir) / "history.jsonl"

        history = InputHistory(
            project_root="/test/project",
            session_id="session-1",
            history_file=history_file
        )

        # Add various commands
        history.add_to_history("git status")
        history.add_to_history("git commit -m 'test'")
        history.add_to_history("npm install")
        history.add_to_history("git push origin main")

        # Search for "git"
        results = history.search_history("git")
        print(f"Search 'git': found {len(results)} results")
        assert len(results) == 3  # Should find 3 git commands

        # Search for "commit"
        results = history.search_history("commit")
        print(f"Search 'commit': found {len(results)} results")
        assert len(results) == 1
        assert results[0][0].display == "git commit -m 'test'"

        # Search for "npm"
        results = history.search_history("npm")
        print(f"Search 'npm': found {len(results)} results")
        assert len(results) == 1
        assert results[0][0].display == "npm install"

        print("✓ Search works correctly")

def test_remove_last():
    """Test removing last entry (for Esc interrupt)"""
    print("\n=== Test 5: Remove Last Entry ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        history_file = Path(tmpdir) / "history.jsonl"

        history = InputHistory(
            project_root="/test/project",
            session_id="session-1",
            history_file=history_file
        )

        # Add commands
        history.add_to_history("command 1")
        history.add_to_history("command 2")
        history.add_to_history("command 3")

        # Remove last
        history.remove_last_from_history()

        # Get history - should only have 2 entries
        entries = history.get_history()
        assert len(entries) == 2
        assert entries[0].display == "command 2"
        assert entries[1].display == "command 1"

        print("✓ Last entry removed successfully")

def test_pasted_content():
    """Test pasted content handling"""
    print("\n=== Test 6: Pasted Content ===")

    with tempfile.TemporaryDirectory() as tmpdir:
        history_file = Path(tmpdir) / "history.jsonl"

        history = InputHistory(
            project_root="/test/project",
            session_id="session-1",
            history_file=history_file
        )

        # Add command with pasted content
        pasted = {
            1: PastedContent(
                id=1,
                type='text',
                content='def hello():\n    print("Hello")'
            )
        }

        history.add_to_history("Here is some code: [Pasted text #1]", pasted)

        # Retrieve and check
        entries = history.get_history()
        assert len(entries) == 1
        assert entries[0].display == "Here is some code: [Pasted text #1]"
        assert 1 in entries[0].pasted_contents
        assert entries[0].pasted_contents[1].content == 'def hello():\n    print("Hello")'

        print("✓ Pasted content stored and retrieved correctly")

def test_format_functions():
    """Test formatting functions"""
    print("\n=== Test 7: Format Functions ===")

    # Test pasted text ref
    ref = format_pasted_text_ref(1, 0)
    assert ref == "[Pasted text #1]"

    ref = format_pasted_text_ref(2, 5)
    assert ref == "[Pasted text #2 +5 lines]"

    # Test image ref
    ref = format_image_ref(3)
    assert ref == "[Image #3]"

    print("✓ Format functions work correctly")

def test_expand_refs():
    """Test expanding pasted text references"""
    print("\n=== Test 8: Expand References ===")

    pasted = {
        1: PastedContent(id=1, type='text', content='Hello World'),
        2: PastedContent(id=2, type='text', content='def foo():\n    pass')
    }

    input_text = "Check this: [Pasted text #1] and [Pasted text #2 +1 lines]"
    expanded = expand_pasted_text_refs(input_text, pasted)

    assert expanded == "Check this: Hello World and def foo():\n    pass"
    print("✓ References expanded correctly")

def run_all_tests():
    """Run all tests"""
    print("=" * 60)
    print("Testing Input History Functionality")
    print("=" * 60)

    try:
        test_basic_history()
        test_session_priority()
        test_project_isolation()
        test_search_history()
        test_remove_last()
        test_pasted_content()
        test_format_functions()
        test_expand_refs()

        print("\n" + "=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)

    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    run_all_tests()
