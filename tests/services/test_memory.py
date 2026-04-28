"""
Tests for memory modules: paths, scan, prompts, extract.
"""

import os
import tempfile
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from codo.services.memory.paths import (
    sanitize_path_for_dir,
    get_project_memory_dir,
    ensure_memory_dir,
    get_memory_index_path,
    is_memory_path,
    ENTRYPOINT_NAME,
    MAX_ENTRYPOINT_LINES,
    MAX_ENTRYPOINT_BYTES,
)
from codo.services.memory.scan import (
    parse_frontmatter,
    scan_memory_files,
    format_memory_manifest,
    load_memory_index,
    MemoryHeader,
)
from codo.services.memory.prompts import (
    build_extract_prompt,
    MEMORY_TYPES,
    WHAT_NOT_TO_SAVE,
)
from codo.services.memory.extract import (
    MemoryExtractionState,
    _count_model_visible_since,
    _has_memory_writes_since,
    _execute_memory_write,
    _execute_memory_edit,
    _process_tool_calls,
)

# ============================================================================
# Paths tests
# ============================================================================

class TestSanitizePath:
    def test_windows_path(self):
        result = sanitize_path_for_dir("C:\\Users\\user\\project")
        assert "\\" not in result
        assert ":" not in result

    def test_unix_path(self):
        result = sanitize_path_for_dir("/home/user/project")
        assert "/" not in result

    def test_collapses_dashes(self):
        result = sanitize_path_for_dir("C:\\\\Users\\\\user")
        assert "--" not in result

    def test_strips_leading_trailing_dashes(self):
        result = sanitize_path_for_dir("/project/")
        assert not result.startswith("-")
        assert not result.endswith("-")

class TestGetProjectMemoryDir:
    def test_returns_path_object(self):
        result = get_project_memory_dir("/home/user/project")
        assert isinstance(result, Path)
        assert "memory" in str(result)
        assert "projects" in str(result)

class TestEnsureMemoryDir:
    def test_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(
                "codo.services.memory.paths.get_project_memory_dir",
                return_value=Path(tmpdir) / "memory",
            ):
                result = ensure_memory_dir("/fake/cwd")
                assert result.exists()
                assert result.is_dir()

class TestGetMemoryIndexPath:
    def test_returns_memory_md_path(self):
        result = get_memory_index_path("/home/user/project")
        assert result.name == ENTRYPOINT_NAME

class TestIsMemoryPath:
    def test_inside_memory_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir) / "memory"
            memory_dir.mkdir()
            test_file = memory_dir / "test.md"
            test_file.touch()

            with patch(
                "codo.services.memory.paths.get_project_memory_dir",
                return_value=memory_dir,
            ):
                assert is_memory_path(str(test_file), "/fake/cwd")

    def test_outside_memory_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir) / "memory"
            memory_dir.mkdir()
            outside_file = Path(tmpdir) / "outside.md"
            outside_file.touch()

            with patch(
                "codo.services.memory.paths.get_project_memory_dir",
                return_value=memory_dir,
            ):
                assert not is_memory_path(str(outside_file), "/fake/cwd")

# ============================================================================
# Scan tests
# ============================================================================

class TestParseFrontmatter:
    def test_valid_frontmatter(self):
        content = """---
description: My memory
type: preference
---

Content here.
"""
        result = parse_frontmatter(content)
        assert result["description"] == "My memory"
        assert result["type"] == "preference"

    def test_no_frontmatter(self):
        result = parse_frontmatter("Just plain text.")
        assert result == {}

    def test_unclosed_frontmatter(self):
        result = parse_frontmatter("---\ndescription: test\nno closing")
        assert result == {}

    def test_empty_content(self):
        result = parse_frontmatter("")
        assert result == {}

    def test_colon_in_value(self):
        content = "---\ndescription: key: value pair\n---\n"
        result = parse_frontmatter(content)
        assert result["description"] == "key: value pair"

class TestScanMemoryFiles:
    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = scan_memory_files(tmpdir)
            assert result == []

    def test_nonexistent_directory(self):
        result = scan_memory_files("/nonexistent/dir")
        assert result == []

    def test_scans_md_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a memory file with frontmatter
            (Path(tmpdir) / "test.md").write_text(
                "---\ndescription: Test memory\ntype: preference\n---\nContent",
                encoding="utf-8",
            )
            # Create MEMORY.md (should be skipped)
            (Path(tmpdir) / ENTRYPOINT_NAME).write_text(
                "- [Test](test.md)",
                encoding="utf-8",
            )

            result = scan_memory_files(tmpdir)
            assert len(result) == 1
            assert result[0].filename == "test.md"
            assert result[0].description == "Test memory"
            assert result[0].memory_type == "preference"

    def test_sorted_by_mtime(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            import time

            (Path(tmpdir) / "old.md").write_text("---\ndescription: Old\n---\n")
            time.sleep(0.1)
            (Path(tmpdir) / "new.md").write_text("---\ndescription: New\n---\n")

            result = scan_memory_files(tmpdir)
            assert len(result) == 2
            assert result[0].filename == "new.md"
            assert result[1].filename == "old.md"

class TestFormatMemoryManifest:
    def test_empty(self):
        assert format_memory_manifest([]) == ""

    def test_single_header(self):
        headers = [
            MemoryHeader(
                filename="prefs.md",
                filepath="/path/prefs.md",
                mtime=0,
                description="User preferences",
                memory_type="preference",
            )
        ]
        result = format_memory_manifest(headers)
        assert "prefs.md" in result
        assert "preference" in result
        assert "User preferences" in result

    def test_no_description(self):
        headers = [
            MemoryHeader(
                filename="note.md",
                filepath="/path/note.md",
                mtime=0,
                description=None,
                memory_type=None,
            )
        ]
        result = format_memory_manifest(headers)
        assert "note.md" in result

class TestLoadMemoryIndex:
    def test_no_index_file(self):
        result = load_memory_index("/nonexistent/path")
        assert result is None

    def test_empty_index(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir) / "memory"
            memory_dir.mkdir()
            (memory_dir / ENTRYPOINT_NAME).write_text("", encoding="utf-8")

            with patch(
                "codo.services.memory.paths.get_memory_index_path",
                return_value=memory_dir / ENTRYPOINT_NAME,
            ):
                result = load_memory_index("/fake/cwd")
                assert result is None

    def test_loads_content(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir) / "memory"
            memory_dir.mkdir()
            index_content = "- [Prefs](prefs.md) — User preferences"
            (memory_dir / ENTRYPOINT_NAME).write_text(
                index_content, encoding="utf-8"
            )

            with patch(
                "codo.services.memory.paths.get_memory_index_path",
                return_value=memory_dir / ENTRYPOINT_NAME,
            ):
                result = load_memory_index("/fake/cwd")
                assert result == index_content

    def test_line_truncation(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir) / "memory"
            memory_dir.mkdir()
            # Create content with more than MAX_ENTRYPOINT_LINES
            lines = [f"- Line {i}" for i in range(MAX_ENTRYPOINT_LINES + 50)]
            content = "\n".join(lines)
            (memory_dir / ENTRYPOINT_NAME).write_text(
                content, encoding="utf-8"
            )

            with patch(
                "codo.services.memory.paths.get_memory_index_path",
                return_value=memory_dir / ENTRYPOINT_NAME,
            ):
                result = load_memory_index("/fake/cwd")
                assert "Truncated" in result
                assert f"line cap ({MAX_ENTRYPOINT_LINES})" in result

# ============================================================================
# Prompts tests
# ============================================================================

class TestBuildExtractPrompt:
    def test_basic_prompt(self):
        prompt = build_extract_prompt(
            new_message_count=10,
            existing_memories="",
            memory_dir="/path/to/memory",
        )
        assert "10" in prompt
        assert "/path/to/memory" in prompt
        assert "memory extraction subagent" in prompt

    def test_with_existing_memories(self):
        prompt = build_extract_prompt(
            new_message_count=5,
            existing_memories="- prefs.md [preference] — User preferences",
            memory_dir="/path/to/memory",
        )
        assert "prefs.md" in prompt
        assert "Existing memory files" in prompt

    def test_includes_types_and_guidelines(self):
        prompt = build_extract_prompt(
            new_message_count=5,
            existing_memories="",
            memory_dir="/path",
        )
        assert "Memory types" in prompt
        assert "What NOT to save" in prompt
        assert "MEMORY.md" in prompt

# ============================================================================
# Extract tests
# ============================================================================

class TestMemoryExtractionState:
    def test_initial_state(self):
        state = MemoryExtractionState()
        assert state.last_message_uuid is None
        assert not state.in_progress
        assert state.turns_since_last_extraction == 0
        assert state.extraction_interval == 1
        assert state.last_written_paths == []

class TestCountModelVisibleSince:
    def test_count_all_when_no_cursor(self):
        messages = [
            {"role": "user", "uuid": "1"},
            {"role": "assistant", "uuid": "2"},
            {"role": "user", "uuid": "3", "content": [{"type": "tool_result"}]},
            {"role": "assistant", "uuid": "4"},
        ]
        assert _count_model_visible_since(messages, None) == 4

    def test_count_since_uuid(self):
        messages = [
            {"role": "user", "uuid": "1"},
            {"role": "assistant", "uuid": "2"},
            {"role": "user", "uuid": "3"},
            {"role": "assistant", "uuid": "4"},
        ]
        assert _count_model_visible_since(messages, "2") == 2

    def test_fallback_when_uuid_not_found(self):
        messages = [
            {"role": "user", "uuid": "1"},
            {"role": "assistant", "uuid": "2"},
        ]
        # UUID "missing" not found — should count all
        assert _count_model_visible_since(messages, "missing") == 2

    def test_empty_messages(self):
        assert _count_model_visible_since([], None) == 0

class TestHasMemoryWritesSince:
    def test_no_memory_writes(self):
        messages = [
            {"role": "user", "uuid": "1"},
            {
                "role": "assistant",
                "uuid": "2",
                "content": [{"type": "text", "text": "hello"}],
            },
        ]
        assert not _has_memory_writes_since(messages, None, "/fake/cwd")

    def test_detects_write_to_memory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = Path(tmpdir) / "memory"
            memory_dir.mkdir()

            with patch(
                "codo.services.memory.extract.is_memory_path",
                return_value=True,
            ):
                messages = [
                    {
                        "role": "assistant",
                        "uuid": "1",
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Write",
                                "input": {"file_path": str(memory_dir / "test.md")},
                            }
                        ],
                    },
                ]
                assert _has_memory_writes_since(messages, None, tmpdir)

    def test_ignores_non_write_tools(self):
        messages = [
            {
                "role": "assistant",
                "uuid": "1",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "/some/file.py"},
                    }
                ],
            },
        ]
        assert not _has_memory_writes_since(messages, None, "/fake/cwd")

class TestExecuteMemoryWrite:
    def test_write_inside_memory_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.md")
            assert _execute_memory_write(file_path, "hello", tmpdir)
            assert Path(file_path).read_text(encoding="utf-8") == "hello"

    def test_write_blocked_outside_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = os.path.join(tmpdir, "memory")
            os.makedirs(memory_dir)
            outside_path = os.path.join(tmpdir, "outside.md")
            assert not _execute_memory_write(outside_path, "bad", memory_dir)
            assert not Path(outside_path).exists()

    def test_creates_subdirectories(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "sub", "dir", "test.md")
            assert _execute_memory_write(file_path, "content", tmpdir)
            assert Path(file_path).exists()

class TestExecuteMemoryEdit:
    def test_edit_replaces_text(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.md")
            Path(file_path).write_text("hello world", encoding="utf-8")
            assert _execute_memory_edit(file_path, "hello", "goodbye", tmpdir)
            assert Path(file_path).read_text(encoding="utf-8") == "goodbye world"

    def test_edit_blocked_outside_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            memory_dir = os.path.join(tmpdir, "memory")
            os.makedirs(memory_dir)
            outside_path = os.path.join(tmpdir, "outside.md")
            Path(outside_path).write_text("content", encoding="utf-8")
            assert not _execute_memory_edit(
                outside_path, "content", "new", memory_dir
            )

    def test_edit_nonexistent_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "missing.md")
            assert not _execute_memory_edit(file_path, "old", "new", tmpdir)

    def test_edit_old_string_not_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.md")
            Path(file_path).write_text("hello", encoding="utf-8")
            assert not _execute_memory_edit(
                file_path, "nonexistent", "new", tmpdir
            )

class TestProcessToolCalls:
    def test_processes_write(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            content = [
                {
                    "type": "tool_use",
                    "name": "Write",
                    "input": {
                        "file_path": os.path.join(tmpdir, "test.md"),
                        "content": "hello",
                    },
                }
            ]
            paths = _process_tool_calls(content, tmpdir)
            assert len(paths) == 1
            assert Path(paths[0]).read_text(encoding="utf-8") == "hello"

    def test_processes_edit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.md")
            Path(file_path).write_text("old text", encoding="utf-8")

            content = [
                {
                    "type": "tool_use",
                    "name": "Edit",
                    "input": {
                        "file_path": file_path,
                        "old_string": "old",
                        "new_string": "new",
                    },
                }
            ]
            paths = _process_tool_calls(content, tmpdir)
            assert len(paths) == 1
            assert Path(file_path).read_text(encoding="utf-8") == "new text"

    def test_ignores_read(self):
        content = [
            {
                "type": "tool_use",
                "name": "Read",
                "input": {"file_path": "/some/file"},
            }
        ]
        paths = _process_tool_calls(content, "/tmp")
        assert paths == []

    def test_ignores_text_blocks(self):
        content = [
            {"type": "text", "text": "some text"},
        ]
        paths = _process_tool_calls(content, "/tmp")
        assert paths == []

    def test_deduplicates_paths(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "test.md")
            content = [
                {
                    "type": "tool_use",
                    "name": "Write",
                    "input": {"file_path": file_path, "content": "v1"},
                },
                {
                    "type": "tool_use",
                    "name": "Write",
                    "input": {"file_path": file_path, "content": "v2"},
                },
            ]
            paths = _process_tool_calls(content, tmpdir)
            assert len(paths) == 1

class TestExtractMemoriesIntegration:
    """Integration-level tests for extract_memories (mocked API)."""

    @pytest.mark.asyncio
    async def test_skips_when_in_progress(self):
        state = MemoryExtractionState()
        state.in_progress = True

        result = await self._run_extract(state=state)
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_when_too_few_messages(self):
        state = MemoryExtractionState()
        messages = [{"role": "user", "uuid": "1"}]

        result = await self._run_extract(messages=messages, state=state)
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_on_interval(self):
        state = MemoryExtractionState()
        state.extraction_interval = 3
        state.turns_since_last_extraction = 0

        messages = [
            {"role": "user", "uuid": "1"},
            {"role": "assistant", "uuid": "2"},
        ]

        result = await self._run_extract(messages=messages, state=state)
        assert result == []
        assert state.turns_since_last_extraction == 1

    async def _run_extract(
        self,
        messages=None,
        state=None,
    ):
        """Helper to run extract_memories with mocked client."""
        from codo.services.memory.extract import extract_memories

        if messages is None:
            messages = []
        if state is None:
            state = MemoryExtractionState()

        mock_client = AsyncMock()
        return await extract_memories(
            client=mock_client,
            model="test-model",
            messages=messages,
            cwd="/fake/cwd",
            state=state,
        )
