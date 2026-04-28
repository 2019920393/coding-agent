"""
会话标题生成和导出功能测试

测试覆盖：
- extract_conversation_text: 对话文本提取
- generate_default_filename: 默认文件名生成
- sanitize_filename: 文件名清理
- messages_to_markdown: Markdown 格式转换
- messages_to_plain_text: 纯文本格式转换
- export_session_to_string: 字符串导出
"""

import json
import os
import tempfile
from typing import Any, Dict, List

import pytest

from codo.session.title import (
    extract_conversation_text,
    MAX_CONVERSATION_TEXT,
)
from codo.session.export import (
    extract_first_prompt,
    sanitize_filename,
    generate_default_filename,
    messages_to_markdown,
    messages_to_plain_text,
    export_session_to_string,
    export_session,
)

# ============================================================================
# 测试数据
# ============================================================================

SAMPLE_MESSAGES: List[Dict[str, Any]] = [
    {
        "role": "user",
        "content": "帮我修复登录按钮的 bug",
        "uuid": "uuid-1",
    },
    {
        "role": "assistant",
        "content": "我来帮你查看登录按钮的问题。",
        "uuid": "uuid-2",
    },
    {
        "role": "user",
        "content": "谢谢，问题已经解决了",
        "uuid": "uuid-3",
    },
]

CONTENT_BLOCK_MESSAGES: List[Dict[str, Any]] = [
    {
        "role": "user",
        "content": [
            {"type": "text", "text": "请帮我分析这段代码"},
        ],
        "uuid": "uuid-1",
    },
    {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "好的，我来分析"},
            {"type": "tool_use", "id": "tool-1", "name": "Read", "input": {"file_path": "/test.py"}},
        ],
        "uuid": "uuid-2",
    },
]

# ============================================================================
# extract_conversation_text 测试
# ============================================================================

class TestExtractConversationText:
    """测试对话文本提取"""

    def test_基本提取(self):
        """普通消息应正确提取文本"""
        text = extract_conversation_text(SAMPLE_MESSAGES)
        assert "帮我修复登录按钮的 bug" in text
        assert "我来帮你查看登录按钮的问题" in text

    def test_跳过非对话消息(self):
        """非 user/assistant 消息应被跳过"""
        messages = [
            {"role": "system", "content": "系统消息"},
            {"role": "user", "content": "用户消息"},
        ]
        text = extract_conversation_text(messages)
        assert "系统消息" not in text
        assert "用户消息" in text

    def test_跳过meta消息(self):
        """isMeta=True 的消息应被跳过"""
        messages = [
            {"role": "user", "content": "正常消息"},
            {"role": "user", "content": "meta 消息", "isMeta": True},
        ]
        text = extract_conversation_text(messages)
        assert "正常消息" in text
        assert "meta 消息" not in text

    def test_content_blocks格式(self):
        """content blocks 格式应正确提取文本"""
        text = extract_conversation_text(CONTENT_BLOCK_MESSAGES)
        assert "请帮我分析这段代码" in text
        assert "好的，我来分析" in text

    def test_长文本截断(self):
        """超过 MAX_CONVERSATION_TEXT 的文本应被截断"""
        long_content = "x" * (MAX_CONVERSATION_TEXT + 100)
        messages = [{"role": "user", "content": long_content}]
        text = extract_conversation_text(messages)
        assert len(text) <= MAX_CONVERSATION_TEXT

    def test_空消息列表(self):
        """空消息列表应返回空字符串"""
        text = extract_conversation_text([])
        assert text == ""

# ============================================================================
# extract_first_prompt 测试
# ============================================================================

class TestExtractFirstPrompt:
    """测试第一条用户消息提取"""

    def test_基本提取(self):
        """应提取第一条用户消息"""
        prompt = extract_first_prompt(SAMPLE_MESSAGES)
        assert prompt == "帮我修复登录按钮的 bug"

    def test_只取第一行(self):
        """多行消息只取第一行"""
        messages = [{"role": "user", "content": "第一行\n第二行\n第三行"}]
        prompt = extract_first_prompt(messages)
        assert prompt == "第一行"
        assert "第二行" not in prompt

    def test_长度限制50字符(self):
        """超过 50 字符的消息应被截断"""
        long_text = "a" * 60
        messages = [{"role": "user", "content": long_text}]
        prompt = extract_first_prompt(messages)
        assert len(prompt) <= 51  # 50 + 省略号

    def test_content_blocks格式(self):
        """content blocks 格式应正确提取"""
        messages = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "分析代码"}],
            }
        ]
        prompt = extract_first_prompt(messages)
        assert prompt == "分析代码"

    def test_无用户消息返回空字符串(self):
        """没有用户消息时返回空字符串"""
        messages = [{"role": "assistant", "content": "助手消息"}]
        prompt = extract_first_prompt(messages)
        assert prompt == ""

# ============================================================================
# sanitize_filename 测试
# ============================================================================

class TestSanitizeFilename:
    """测试文件名清理"""

    def test_基本清理(self):
        """普通文本应转换为安全文件名"""
        result = sanitize_filename("Fix login button")
        assert result == "fix-login-button"

    def test_移除特殊字符(self):
        """特殊字符应被移除"""
        result = sanitize_filename("Fix: login/button!")
        assert "/" not in result
        assert ":" not in result
        assert "!" not in result

    def test_空格转连字符(self):
        """空格应转换为连字符"""
        result = sanitize_filename("hello world")
        assert result == "hello-world"

    def test_合并多个连字符(self):
        """多个连字符应合并为一个"""
        result = sanitize_filename("hello   world")
        assert "--" not in result

    def test_移除首尾连字符(self):
        """首尾连字符应被移除"""
        result = sanitize_filename("  hello  ")
        assert not result.startswith("-")
        assert not result.endswith("-")

# ============================================================================
# generate_default_filename 测试
# ============================================================================

class TestGenerateDefaultFilename:
    """测试默认文件名生成"""

    def test_有消息时包含提示词(self):
        """有用户消息时文件名应包含提示词"""
        filename = generate_default_filename(SAMPLE_MESSAGES)
        assert "bug" in filename or "login" in filename

    def test_无消息时使用conversation前缀(self):
        """无消息时文件名应以 conversation- 开头"""
        filename = generate_default_filename([])
        assert filename.startswith("conversation-")

    def test_默认扩展名为txt(self):
        """默认扩展名应为 .txt"""
        filename = generate_default_filename(SAMPLE_MESSAGES)
        assert filename.endswith(".txt")

    def test_自定义扩展名(self):
        """自定义扩展名应被正确应用"""
        filename = generate_default_filename(SAMPLE_MESSAGES, extension=".md")
        assert filename.endswith(".md")

# ============================================================================
# messages_to_markdown 测试
# ============================================================================

class TestMessagesToMarkdown:
    """测试 Markdown 格式转换"""

    def test_包含用户消息(self):
        """Markdown 应包含用户消息内容"""
        md = messages_to_markdown(SAMPLE_MESSAGES)
        assert "帮我修复登录按钮的 bug" in md

    def test_包含助手消息(self):
        """Markdown 应包含助手消息内容"""
        md = messages_to_markdown(SAMPLE_MESSAGES)
        assert "我来帮你查看登录按钮的问题" in md

    def test_包含角色标题(self):
        """Markdown 应包含角色标题"""
        md = messages_to_markdown(SAMPLE_MESSAGES)
        assert "## 用户" in md
        assert "## 助手" in md

    def test_包含导出时间(self):
        """Markdown 应包含导出时间"""
        md = messages_to_markdown(SAMPLE_MESSAGES)
        assert "导出时间" in md

    def test_工具调用显示摘要(self):
        """工具调用应显示摘要而非完整内容"""
        md = messages_to_markdown(CONTENT_BLOCK_MESSAGES)
        assert "工具调用" in md or "Read" in md

# ============================================================================
# messages_to_plain_text 测试
# ============================================================================

class TestMessagesToPlainText:
    """测试纯文本格式转换"""

    def test_包含用户前缀(self):
        """纯文本应包含 Human: 前缀"""
        text = messages_to_plain_text(SAMPLE_MESSAGES)
        assert "Human:" in text

    def test_包含助手前缀(self):
        """纯文本应包含 Assistant: 前缀"""
        text = messages_to_plain_text(SAMPLE_MESSAGES)
        assert "Assistant:" in text

    def test_包含消息内容(self):
        """纯文本应包含消息内容"""
        text = messages_to_plain_text(SAMPLE_MESSAGES)
        assert "帮我修复登录按钮的 bug" in text

    def test_跳过meta消息(self):
        """meta 消息应被跳过"""
        messages = [
            {"role": "user", "content": "正常消息"},
            {"role": "user", "content": "meta 消息", "isMeta": True},
        ]
        text = messages_to_plain_text(messages)
        assert "meta 消息" not in text

# ============================================================================
# export_session_to_string 测试
# ============================================================================

class TestExportSessionToString:
    """测试字符串导出"""

    def test_txt格式(self):
        """txt 格式应返回纯文本"""
        content = export_session_to_string(SAMPLE_MESSAGES, format="txt")
        assert "Human:" in content
        assert "Assistant:" in content

    def test_md格式(self):
        """md 格式应返回 Markdown"""
        content = export_session_to_string(SAMPLE_MESSAGES, format="md")
        assert "## 用户" in content
        assert "## 助手" in content

    def test_json格式(self):
        """json 格式应返回有效 JSON"""
        content = export_session_to_string(SAMPLE_MESSAGES, format="json")
        data = json.loads(content)
        assert "messages" in data
        assert "exported_at" in data
        assert len(data["messages"]) > 0

# ============================================================================
# export_session 测试
# ============================================================================

class TestExportSession:
    """测试文件导出"""

    def test_导出到txt文件(self, tmp_path):
        """应成功导出到 txt 文件"""
        output_path = str(tmp_path / "test.txt")
        result = export_session(SAMPLE_MESSAGES, output_path, format="txt")
        assert result == output_path
        assert os.path.exists(output_path)
        content = open(output_path, encoding="utf-8").read()
        assert "Human:" in content

    def test_导出到md文件(self, tmp_path):
        """应成功导出到 md 文件"""
        output_path = str(tmp_path / "test.md")
        result = export_session(SAMPLE_MESSAGES, output_path, format="md")
        assert os.path.exists(output_path)
        content = open(output_path, encoding="utf-8").read()
        assert "## 用户" in content

    def test_导出到json文件(self, tmp_path):
        """应成功导出到 json 文件"""
        output_path = str(tmp_path / "test.json")
        result = export_session(SAMPLE_MESSAGES, output_path, format="json")
        assert os.path.exists(output_path)
        data = json.loads(open(output_path, encoding="utf-8").read())
        assert "messages" in data

    def test_自动创建目录(self, tmp_path):
        """应自动创建不存在的目录"""
        output_path = str(tmp_path / "subdir" / "test.txt")
        export_session(SAMPLE_MESSAGES, output_path)
        assert os.path.exists(output_path)
