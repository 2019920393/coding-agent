"""
AgentTool 测试

覆盖：
- types: 输入/输出 schema 验证
- agents: 内置代理定义、查找
- utils: 工具过滤、文本提取
- prompt: prompt 生成
- agent_tool: 核心执行逻辑（mock API）
"""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

from codo.tools.agent_tool.types import AgentToolInput, AgentToolOutput
from codo.tools.agent_tool.agents import (
    AgentDefinition,
    EXPLORE_AGENT,
    PLAN_AGENT,
    BUILTIN_AGENTS,
    get_builtin_agents,
    find_agent_by_type,
)
from codo.tools.agent_tool.utils import (
    filter_tools_for_agent,
    extract_final_text,
    ALL_AGENT_DISALLOWED_TOOLS,
)
from codo.tools.agent_tool.prompt import (
    AGENT_TOOL_NAME,
    LEGACY_AGENT_TOOL_NAME,
    MAX_AGENT_TURNS,
    get_agent_tool_prompt,
)
from codo.tools import receipts as tool_receipts
from codo.tools.agent_tool.agent_tool import (
    AgentTool,
    agent_tool,
    _run_sub_agent,
    _execute_agent_tool,
    _tool_to_schema,
)
from codo.tools.receipts import AuditLogEvent, CommandReceipt
from codo.tools.types import ToolResult
from codo.services.tools.permission_checker import create_default_permission_context
from codo.types.permissions import PermissionAskDecision

# ============================================================================
# Types tests
# ============================================================================

class TestAgentToolInput:
    def test_basic_input(self):
        inp = AgentToolInput(
            description="Find auth files",
            prompt="Search for authentication middleware",
        )
        assert inp.description == "Find auth files"
        assert inp.prompt == "Search for authentication middleware"
        assert inp.subagent_type is None
        assert inp.run_in_background is False

    def test_with_subagent_type(self):
        inp = AgentToolInput(
            description="Plan feature",
            prompt="Design auth system",
            subagent_type="Plan",
        )
        assert inp.subagent_type == "Plan"

    def test_description_required(self):
        with pytest.raises(Exception):
            AgentToolInput(prompt="test")

    def test_prompt_required(self):
        with pytest.raises(Exception):
            AgentToolInput(description="test")

class TestAgentToolOutput:
    def test_basic_output(self):
        out = AgentToolOutput(result="Found 3 files")
        assert out.result == "Found 3 files"
        assert out.total_tokens == 0
        assert out.input_tokens == 0
        assert out.output_tokens == 0
        assert out.background is False
        assert out.task_id is None

    def test_with_tokens(self):
        out = AgentToolOutput(
            result="Done",
            total_tokens=1000,
            input_tokens=600,
            output_tokens=400,
        )
        assert out.total_tokens == 1000

# ============================================================================
# Agents tests
# ============================================================================

class TestAgentDefinitions:
    def test_explore_agent_exists(self):
        assert EXPLORE_AGENT.agent_type == "Explore"
        assert EXPLORE_AGENT.is_read_only is True
        assert "Agent" in EXPLORE_AGENT.disallowed_tools
        assert "Edit" in EXPLORE_AGENT.disallowed_tools
        assert "Write" in EXPLORE_AGENT.disallowed_tools
        assert EXPLORE_AGENT.model is not None  # haiku

    def test_plan_agent_exists(self):
        assert PLAN_AGENT.agent_type == "Plan"
        assert PLAN_AGENT.is_read_only is True
        assert "Agent" in PLAN_AGENT.disallowed_tools
        assert PLAN_AGENT.model is None  # inherit

    def test_explore_system_prompt_content(self):
        assert "READ-ONLY" in EXPLORE_AGENT.system_prompt
        assert "file search specialist" in EXPLORE_AGENT.system_prompt

    def test_plan_system_prompt_content(self):
        assert "READ-ONLY" in PLAN_AGENT.system_prompt
        assert "software architect" in PLAN_AGENT.system_prompt.lower()

    def test_builtin_agents_registry(self):
        assert "Explore" in BUILTIN_AGENTS
        assert "Plan" in BUILTIN_AGENTS
        assert len(BUILTIN_AGENTS) == 2

    def test_get_builtin_agents_returns_copy(self):
        agents = get_builtin_agents()
        agents["Fake"] = None
        assert "Fake" not in BUILTIN_AGENTS

    def test_find_agent_by_type_found(self):
        agent = find_agent_by_type("Explore")
        assert agent is EXPLORE_AGENT

    def test_find_agent_by_type_not_found(self):
        agent = find_agent_by_type("NonExistent")
        assert agent is None

# ============================================================================
# Utils tests
# ============================================================================

class TestFilterToolsForAgent:
    def _make_tool(self, name):
        tool = MagicMock()
        tool.name = name
        return tool

    def test_filters_disallowed_tools(self):
        tools = [self._make_tool("Bash"), self._make_tool("Agent"), self._make_tool("Read")]
        agent_def = AgentDefinition(
            agent_type="Test",
            when_to_use="test",
            system_prompt="test",
            disallowed_tools=["Agent"],
        )
        result = filter_tools_for_agent(tools, agent_def)
        names = [t.name for t in result]
        assert "Agent" not in names
        assert "Bash" in names
        assert "Read" in names

    def test_filters_agent_specific_disallowed(self):
        tools = [self._make_tool("Bash"), self._make_tool("Edit"), self._make_tool("Write")]
        agent_def = AgentDefinition(
            agent_type="Test",
            when_to_use="test",
            system_prompt="test",
            disallowed_tools=["Edit", "Write"],
        )
        result = filter_tools_for_agent(tools, agent_def)
        names = [t.name for t in result]
        assert "Bash" in names
        assert "Edit" not in names
        assert "Write" not in names

    def test_allows_mcp_tools(self):
        tools = [
            self._make_tool("mcp__slack__send"),
            self._make_tool("Agent"),
        ]
        agent_def = AgentDefinition(
            agent_type="Test",
            when_to_use="test",
            system_prompt="test",
            disallowed_tools=["Agent"],
        )
        result = filter_tools_for_agent(tools, agent_def)
        names = [t.name for t in result]
        assert "mcp__slack__send" in names
        assert "Agent" not in names

    def test_all_agent_disallowed_tools(self):
        """Agent tool is always disallowed for sub-agents"""
        assert "Agent" in ALL_AGENT_DISALLOWED_TOOLS

    def test_explore_agent_filtering(self):
        """Explore agent should only allow read-only tools"""
        tools = [
            self._make_tool("Bash"),
            self._make_tool("Read"),
            self._make_tool("Glob"),
            self._make_tool("Grep"),
            self._make_tool("Edit"),
            self._make_tool("Write"),
            self._make_tool("Agent"),
        ]
        result = filter_tools_for_agent(tools, EXPLORE_AGENT)
        names = [t.name for t in result]
        assert "Bash" in names
        assert "Read" in names
        assert "Glob" in names
        assert "Grep" in names
        assert "Edit" not in names
        assert "Write" not in names
        assert "Agent" not in names

class TestExtractFinalText:
    def test_extracts_text_from_content_blocks(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Here is the result"},
            ]},
        ]
        assert extract_final_text(messages) == "Here is the result"

    def test_extracts_from_last_assistant(self):
        messages = [
            {"role": "assistant", "content": [{"type": "text", "text": "old"}]},
            {"role": "user", "content": "more"},
            {"role": "assistant", "content": [{"type": "text", "text": "new"}]},
        ]
        assert extract_final_text(messages) == "new"

    def test_extracts_string_content(self):
        messages = [
            {"role": "assistant", "content": "simple string"},
        ]
        assert extract_final_text(messages) == "simple string"

    def test_ignores_tool_use_blocks(self):
        messages = [
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "1", "name": "Bash", "input": {}},
                {"type": "text", "text": "result text"},
            ]},
        ]
        assert extract_final_text(messages) == "result text"

    def test_returns_empty_for_no_messages(self):
        assert extract_final_text([]) == ""

    def test_returns_empty_for_no_assistant(self):
        messages = [{"role": "user", "content": "hello"}]
        assert extract_final_text(messages) == ""

    def test_joins_multiple_text_blocks(self):
        messages = [
            {"role": "assistant", "content": [
                {"type": "text", "text": "Part 1"},
                {"type": "text", "text": "Part 2"},
            ]},
        ]
        assert extract_final_text(messages) == "Part 1\nPart 2"

# ============================================================================
# Prompt tests
# ============================================================================

class TestPrompt:
    def test_constants(self):
        assert AGENT_TOOL_NAME == "Agent"
        assert LEGACY_AGENT_TOOL_NAME == "Task"
        assert MAX_AGENT_TURNS > 0

    def test_get_agent_tool_prompt_default(self):
        prompt = get_agent_tool_prompt()
        assert "Explore" in prompt
        assert "Plan" in prompt
        assert "subagent_type" in prompt

    def test_get_agent_tool_prompt_custom_agents(self):
        custom = [
            AgentDefinition(
                agent_type="Custom",
                when_to_use="For custom tasks",
                system_prompt="custom prompt",
            )
        ]
        prompt = get_agent_tool_prompt(custom)
        assert "Custom" in prompt
        # Custom agent's description should be in the "Available Agent Types" section
        assert "For custom tasks" in prompt

# ============================================================================
# AgentTool class tests
# ============================================================================

class TestAgentToolClass:
    def test_tool_name(self):
        tool = AgentTool()
        assert tool.name == "Agent"

    def test_max_result_size(self):
        tool = AgentTool()
        assert tool.max_result_size_chars == 100_000

    def test_requires_permission_false(self):
        tool = AgentTool()
        inp = AgentToolInput(description="test", prompt="test")
        assert tool.requires_permission(inp) is False

    def test_user_facing_name_default(self):
        tool = AgentTool()
        assert tool.user_facing_name() == "Agent"

    def test_user_facing_name_with_type(self):
        tool = AgentTool()
        inp = AgentToolInput(
            description="test", prompt="test", subagent_type="Explore"
        )
        assert tool.user_facing_name(inp) == "Agent(Explore)"

    def test_activity_description(self):
        tool = AgentTool()
        inp = AgentToolInput(
            description="Find auth", prompt="test", subagent_type="Explore"
        )
        desc = tool.get_activity_description(inp)
        assert "Explore" in desc
        assert "Find auth" in desc

    @pytest.mark.asyncio
    async def test_prompt_method(self):
        tool = AgentTool()
        prompt = await tool.prompt({})
        assert "Explore" in prompt
        assert "Plan" in prompt

    @pytest.mark.asyncio
    async def test_description_method(self):
        tool = AgentTool()
        inp = AgentToolInput(description="test", prompt="test")
        desc = await tool.description(inp, {})
        assert "agent" in desc.lower()

    def test_map_result(self):
        tool = AgentTool()
        output = AgentToolOutput(result="Found 3 files")
        result = tool.map_tool_result_to_tool_result_block_param(output, "test-id")
        assert result["type"] == "tool_result"
        assert result["tool_use_id"] == "test-id"
        assert result["content"] == "Found 3 files"

    def test_module_level_instance(self):
        assert agent_tool is not None
        assert agent_tool.name == "Agent"

# ============================================================================
# Core execution tests (with mocked API)
# ============================================================================

class TestRunSubAgent:
    @pytest.mark.asyncio
    async def test_single_turn_no_tools(self):
        """Sub-agent returns text without tool calls"""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Found 3 files")]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        result_text, usage = await _run_sub_agent(
            client=mock_client,
            model="claude-haiku-4-5-20251001",
            system_prompt="You are a search agent",
            tools=[],
            prompt="Find auth files",
            cwd="/tmp",
        )

        assert "Found 3 files" in result_text
        assert usage["input"] == 100
        assert usage["output"] == 50
        assert usage["total"] == 150

    @pytest.mark.asyncio
    async def test_tool_call_then_response(self):
        """Sub-agent calls a tool, then returns final response"""
        class GlobTool:
            name = "Glob"

            class input_schema:
                def __init__(self, **kwargs):
                    self.pattern = kwargs["pattern"]

                @classmethod
                def model_json_schema(cls):
                    return {"type": "object", "properties": {"pattern": {"type": "string"}}}

            def __init__(self) -> None:
                self.call = AsyncMock(
                    return_value=ToolResult(data="src/auth.py\nsrc/middleware.py")
                )

            def is_concurrency_safe(self, *_args, **_kwargs):
                return False

            async def prompt(self, _options):
                return "Glob tool"

            async def check_permissions(self, input_data, context):
                from codo.types.permissions import create_allow_decision

                return create_allow_decision()

        mock_tool = GlobTool()

        # First API call: tool use
        # Note: MagicMock(name=...) sets _mock_name, not a regular attribute.
        # We need to set .name after construction for tool_use blocks.
        tool_use_block = MagicMock()
        tool_use_block.type = "tool_use"
        tool_use_block.id = "tu-1"
        tool_use_block.name = "Glob"
        tool_use_block.input = {"pattern": "**/*.py"}
        first_response = MagicMock()
        first_response.content = [tool_use_block]
        first_response.usage = MagicMock(input_tokens=80, output_tokens=30)

        # Second API call: text response
        text_block = MagicMock(type="text", text="Found auth files at src/auth.py")
        second_response = MagicMock()
        second_response.content = [text_block]
        second_response.usage = MagicMock(input_tokens=120, output_tokens=40)

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[first_response, second_response]
        )

        result_text, usage = await _run_sub_agent(
            client=mock_client,
            model="claude-haiku-4-5-20251001",
            system_prompt="You are a search agent",
            tools=[mock_tool],
            prompt="Find auth files",
            cwd="/tmp",
        )

        assert "auth files" in result_text
        assert usage["total"] == 80 + 30 + 120 + 40
        assert mock_client.messages.create.call_count == 2
        mock_tool.call.assert_called_once()

    @pytest.mark.asyncio
    async def test_max_turns_limit(self):
        """Sub-agent should stop after max_turns"""
        tool_use_block = MagicMock()
        tool_use_block.type = "tool_use"
        tool_use_block.id = "tu-1"
        tool_use_block.name = "Read"
        tool_use_block.input = {"file_path": "/tmp/test"}
        mock_response = MagicMock()
        mock_response.content = [tool_use_block]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=10)

        class ReadTool:
            name = "Read"

            class input_schema:
                def __init__(self, **kwargs):
                    self.file_path = kwargs["file_path"]

                @classmethod
                def model_json_schema(cls):
                    return {"type": "object", "properties": {"file_path": {"type": "string"}}}

            def __init__(self) -> None:
                self.call = AsyncMock(return_value=ToolResult(data="file content"))

            def is_concurrency_safe(self, *_args, **_kwargs):
                return False

            async def prompt(self, _options):
                return "Read tool"

            async def check_permissions(self, input_data, context):
                from codo.types.permissions import create_allow_decision

                return create_allow_decision()

        mock_tool = ReadTool()

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        result_text, usage = await _run_sub_agent(
            client=mock_client,
            model="test",
            system_prompt="test",
            tools=[mock_tool],
            prompt="test",
            cwd="/tmp",
            max_turns=3,
        )

        # Should have been called exactly max_turns times
        assert mock_client.messages.create.call_count == 3

    @pytest.mark.asyncio
    async def test_no_tool_schemas_when_no_tools(self):
        """When no tools available, API is called without tools parameter"""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Done")]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=10)

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        await _run_sub_agent(
            client=mock_client,
            model="test",
            system_prompt="test",
            tools=[],
            prompt="test",
            cwd="/tmp",
        )

        call_kwargs = mock_client.messages.create.call_args[1]
        assert "tools" not in call_kwargs

    @pytest.mark.asyncio
    async def test_run_sub_agent_emits_structured_child_tool_receipts_and_todos(self):
        events = []

        async def event_callback(event_type, payload):
            events.append((event_type, payload))

        class StructuredTodoTool:
            name = "TodoWrite"

            class input_schema:
                def __init__(self, **kwargs):
                    self.todos = kwargs["todos"]

                @classmethod
                def model_json_schema(cls):
                    return {"type": "object", "properties": {"todos": {"type": "array"}}}

            def is_concurrency_safe(self, *_args, **_kwargs):
                return False

            async def prompt(self, _options):
                return "TodoWrite tool"

            async def check_permissions(self, input_data, context):
                from codo.types.permissions import create_allow_decision

                return create_allow_decision()

            async def call(self, input_data, context, *_args):
                context["options"]["app_state"]["todos"]["agent-child"] = [
                    {
                        "content": "Run targeted tests",
                        "status": "in_progress",
                        "activeForm": "Running targeted tests",
                    },
                    {
                        "content": "Summarize findings",
                        "status": "pending",
                        "activeForm": "Summarizing findings",
                    },
                ]
                return ToolResult(
                    data="12 passed",
                    receipt=CommandReceipt(
                        kind="command",
                        summary="Pytest finished",
                        command="pytest -q",
                        exit_code=0,
                        stdout="12 passed",
                        stderr="",
                    ),
                    audit_events=[
                        AuditLogEvent(
                            event_id="child-audit-1",
                            agent_id="agent-child",
                            source="tool",
                            message="Child pytest completed",
                            created_at=0.0,
                            metadata={"cwd": "/tmp"},
                        )
                    ],
                )

        mock_tool = StructuredTodoTool()

        tool_use_block = MagicMock()
        tool_use_block.type = "tool_use"
        tool_use_block.id = "tu-child-1"
        tool_use_block.name = "TodoWrite"
        tool_use_block.input = {"todos": [{"content": "Run targeted tests"}]}
        first_response = MagicMock()
        first_response.content = [tool_use_block]
        first_response.usage = MagicMock(input_tokens=80, output_tokens=30)

        text_block = MagicMock(type="text", text="Done")
        second_response = MagicMock()
        second_response.content = [text_block]
        second_response.usage = MagicMock(input_tokens=40, output_tokens=20)

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[first_response, second_response])

        result_text, usage = await _run_sub_agent(
            client=mock_client,
            model="claude-haiku-4-5-20251001",
            system_prompt="You are a search agent",
            tools=[mock_tool],
            prompt="Track the work",
            cwd="/tmp",
            agent_id="agent-child",
            event_callback=event_callback,
        )

        assert result_text == "Done"
        assert usage["total"] == 170
        agent_tool_completed = next(payload for event_type, payload in events if event_type == "agent_tool_completed")
        todo_updated = next(payload for event_type, payload in events if event_type == "todo_updated")

        assert agent_tool_completed["receipt"]["kind"] == "command"
        assert agent_tool_completed["audit_events"][0]["message"] == "Child pytest completed"
        assert todo_updated["key"] == "agent-child"
        assert todo_updated["items"][0]["content"] == "Run targeted tests"

    @pytest.mark.asyncio
    async def test_run_sub_agent_routes_child_tool_permission_requests_through_interaction_broker(self, monkeypatch):
        class FakeInteractionBroker:
            def __init__(self) -> None:
                self.requests = []
                self._futures = {}

            async def request(self, request):
                future = asyncio.get_running_loop().create_future()
                self.requests.append(request)
                self._futures[request.request_id] = future
                return await future

            def resolve(self, request_id, data):
                self._futures[request_id].set_result(data)

        class PermissionTool:
            name = "Bash"

            class input_schema:
                def __init__(self, **kwargs):
                    self.command = kwargs["command"]

                @classmethod
                def model_json_schema(cls):
                    return {"type": "object", "properties": {"command": {"type": "string"}}}

            def is_concurrency_safe(self, *_args, **_kwargs):
                return False

            async def prompt(self, _options):
                return "Run a shell command"

            async def call(self, input_data, context, *_args):
                return ToolResult(
                    data="child command ok",
                    receipt=CommandReceipt(
                        kind="command",
                        summary="Child command finished",
                        command=input_data.command,
                        exit_code=0,
                        stdout="ok",
                        stderr="",
                    ),
                )

        async def fake_has_permissions_to_use_tool(*_args, **_kwargs):
            return PermissionAskDecision(message="Need approval for child tool")

        monkeypatch.setattr(
            "codo.services.tools.permission_checker.has_permissions_to_use_tool",
            fake_has_permissions_to_use_tool,
        )

        broker = FakeInteractionBroker()
        tool = PermissionTool()
        events = []

        async def event_callback(event_type, payload):
            events.append((event_type, payload))

        tool_use_block = MagicMock()
        tool_use_block.type = "tool_use"
        tool_use_block.id = "tu-child-permission"
        tool_use_block.name = "Bash"
        tool_use_block.input = {"command": "pytest -q"}
        first_response = MagicMock()
        first_response.content = [tool_use_block]
        first_response.usage = MagicMock(input_tokens=20, output_tokens=10)

        text_block = MagicMock(type="text", text="Done after approval")
        second_response = MagicMock()
        second_response.content = [text_block]
        second_response.usage = MagicMock(input_tokens=10, output_tokens=5)

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=[first_response, second_response])

        run_task = asyncio.create_task(
            _run_sub_agent(
                client=mock_client,
                model="claude-haiku-4-5-20251001",
                system_prompt="You are a child agent",
                tools=[tool],
                prompt="Run child command",
                cwd="/tmp",
                agent_id="agent-child",
                interaction_broker=broker,
                permission_context=create_default_permission_context("/tmp"),
                event_callback=event_callback,
            )
        )

        while not broker.requests:
            await asyncio.sleep(0.01)

        request = broker.requests[0]
        assert request.kind == "permission"
        broker.resolve(request.request_id, "allow_once")

        result_text, usage = await run_task
        assert result_text == "Done after approval"
        assert usage["total"] == 45
        completed = next(payload for event_type, payload in events if event_type == "agent_tool_completed")
        assert completed["receipt"]["kind"] == "command"
        assert completed["content"] == "Child command finished"

class TestExecuteAgentTool:
    @pytest.mark.asyncio
    async def test_tool_found_and_executed(self):
        mock_tool = MagicMock()
        mock_tool.name = "Bash"
        mock_tool.execute = AsyncMock(
            return_value=MagicMock(data="output", error=None)
        )

        result = await _execute_agent_tool(
            [mock_tool], "Bash", {"command": "ls"}, "/tmp"
        )
        assert result["content"] == "output"
        assert result["status"] == "completed"
        mock_tool.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_tool_not_found(self):
        result = await _execute_agent_tool(
            [], "NonExistent", {}, "/tmp"
        )
        assert "not found" in result["content"].lower()
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_tool_execution_error(self):
        mock_tool = MagicMock()
        mock_tool.name = "Bash"
        mock_tool.execute = AsyncMock(side_effect=Exception("Permission denied"))

        result = await _execute_agent_tool(
            [mock_tool], "Bash", {"command": "rm -rf /"}, "/tmp"
        )
        assert "Error" in result["content"]
        assert "Permission denied" in result["content"]
        assert result["status"] == "error"

    @pytest.mark.asyncio
    async def test_tool_returns_error_result(self):
        mock_tool = MagicMock()
        mock_tool.name = "Read"
        mock_tool.execute = AsyncMock(
            return_value=MagicMock(data=None, error="File not found")
        )

        result = await _execute_agent_tool(
            [mock_tool], "Read", {"file_path": "/nonexistent"}, "/tmp"
        )
        assert "Error" in result["content"]
        assert result["status"] == "error"

class TestToolToSchema:
    @pytest.mark.asyncio
    async def test_basic_schema(self):
        mock_tool = MagicMock()
        mock_tool.name = "TestTool"
        mock_tool.prompt = AsyncMock(return_value="A test tool")
        mock_tool.input_schema = MagicMock()
        mock_tool.input_schema.model_json_schema = MagicMock(
            return_value={
                "type": "object",
                "title": "TestInput",
                "properties": {"arg": {"type": "string"}},
            }
        )

        schema = await _tool_to_schema(mock_tool)
        assert schema["name"] == "TestTool"
        assert schema["description"] == "A test tool"
        assert "title" not in schema["input_schema"]
        assert schema["input_schema"]["type"] == "object"

    @pytest.mark.asyncio
    async def test_no_input_schema(self):
        mock_tool = MagicMock()
        mock_tool.name = "Simple"
        mock_tool.prompt = AsyncMock(return_value="Simple tool")
        mock_tool.input_schema = None

        schema = await _tool_to_schema(mock_tool)
        assert schema["input_schema"] == {"type": "object", "properties": {}}

# ============================================================================
# AgentTool.call() integration tests
# ============================================================================

class TestAgentToolCall:
    @pytest.mark.asyncio
    async def test_unknown_agent_type_returns_error(self):
        tool = AgentTool()
        args = AgentToolInput(
            description="test",
            prompt="test",
            subagent_type="NonExistent",
        )
        context = MagicMock()
        context.options = {"api_client": AsyncMock(), "tools": [], "model": "test"}

        result = await tool.call(args, context, lambda: True, None, None)
        assert result.error is not None
        assert "not found" in result.error

    @pytest.mark.asyncio
    async def test_no_api_client_returns_error(self):
        tool = AgentTool()
        args = AgentToolInput(description="test", prompt="test")
        context = MagicMock()
        context.options = {}

        result = await tool.call(args, context, lambda: True, None, None)
        assert result.error is not None
        assert "API client" in result.error

    @pytest.mark.asyncio
    async def test_successful_explore_agent(self):
        """Full flow: AgentTool.call() → _run_sub_agent → return result"""
        tool = AgentTool()
        args = AgentToolInput(
            description="Find auth",
            prompt="Search for authentication files",
            subagent_type="Explore",
        )

        # Mock API response
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Found auth.py")]
        mock_response.usage = MagicMock(input_tokens=100, output_tokens=50)
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        context = MagicMock()
        context.options = {
            "api_client": mock_client,
            "tools": [],
            "model": "claude-sonnet-4-20250514",
            "cwd": "/tmp",
        }

        result = await tool.call(args, context, lambda: True, None, None)
        assert result.data is not None
        assert result.error is None
        assert "auth.py" in result.data.result
        assert result.data.total_tokens == 150

    @pytest.mark.asyncio
    async def test_default_agent_type_is_explore(self):
        """When subagent_type is not specified, should default to Explore"""
        tool = AgentTool()
        args = AgentToolInput(description="Find files", prompt="Find all .py files")

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="result")]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=10)
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        context = MagicMock()
        context.options = {
            "api_client": mock_client,
            "tools": [],
            "model": "test",
            "cwd": "/tmp",
        }

        result = await tool.call(args, context, lambda: True, None, None)
        assert result.data is not None

        # Verify the model used was haiku (Explore agent's model)
        call_kwargs = mock_client.messages.create.call_args[1]
        assert "haiku" in call_kwargs["model"]

    @pytest.mark.asyncio
    async def test_plan_agent_inherits_model(self):
        """Plan agent should inherit parent model"""
        tool = AgentTool()
        args = AgentToolInput(
            description="Design system",
            prompt="Plan auth system",
            subagent_type="Plan",
        )

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="Plan: step 1")]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=10)
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        context = MagicMock()
        context.options = {
            "api_client": mock_client,
            "tools": [],
            "model": "claude-opus-4-20250514",
            "cwd": "/tmp",
        }

        result = await tool.call(args, context, lambda: True, None, None)
        assert result.data is not None

        # Verify Plan uses parent model (opus), not haiku
        call_kwargs = mock_client.messages.create.call_args[1]
        assert "opus" in call_kwargs["model"]

    @pytest.mark.asyncio
    async def test_tools_are_filtered_for_agent(self):
        """AgentTool should filter tools based on agent definition"""
        tool = AgentTool()
        args = AgentToolInput(
            description="Search",
            prompt="Search files",
            subagent_type="Explore",
        )

        # Create mock tools
        bash_tool = MagicMock()
        bash_tool.name = "Bash"
        bash_tool.prompt = AsyncMock(return_value="bash")
        bash_tool.input_schema = None

        edit_tool = MagicMock()
        edit_tool.name = "Edit"

        agent_tool_mock = MagicMock()
        agent_tool_mock.name = "Agent"

        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = [MagicMock(type="text", text="done")]
        mock_response.usage = MagicMock(input_tokens=10, output_tokens=10)
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        context = MagicMock()
        context.options = {
            "api_client": mock_client,
            "tools": [bash_tool, edit_tool, agent_tool_mock],
            "model": "test",
            "cwd": "/tmp",
        }

        await tool.call(args, context, lambda: True, None, None)

        # Check that API was called with only allowed tools
        call_kwargs = mock_client.messages.create.call_args[1]
        tool_names = [t["name"] for t in call_kwargs.get("tools", [])]
        assert "Bash" in tool_names
        assert "Edit" not in tool_names
        assert "Agent" not in tool_names

    @pytest.mark.asyncio
    async def test_api_error_returns_error_result(self):
        """If API call raises, should return error ToolResult"""
        tool = AgentTool()
        args = AgentToolInput(description="test", prompt="test")

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=Exception("API rate limit")
        )

        context = MagicMock()
        context.options = {
            "api_client": mock_client,
            "tools": [],
            "model": "test",
            "cwd": "/tmp",
        }

        result = await tool.call(args, context, lambda: True, None, None)
        assert result.error is not None
        assert "rate limit" in result.error.lower()

    @pytest.mark.asyncio
    async def test_background_agent_returns_task_metadata(self):
        """后台 Agent 调用应立即返回任务信息，并把标记带回输出。"""
        tool = AgentTool()
        args = AgentToolInput(
            description="Search config files",
            prompt="Find config files in the repo",
            subagent_type="Explore",
            run_in_background=True,
        )

        context = MagicMock()
        context.options = {
            "api_client": AsyncMock(),
            "tools": [],
            "model": "claude-sonnet-4-20250514",
            "cwd": "/tmp",
        }

        with patch("codo.team.enhanced_agent.run_subagent_with_mode", new=AsyncMock(return_value={
            "result": "Background task started: task_abc123",
            "mode": "fresh",
            "agent_id": "agent_1",
            "task_id": "task_abc123",
            "status": "running",
            "is_background": True,
        })) as mocked_run:
            result = await tool.call(args, context, lambda: True, None, None)

        assert result.error is None
        assert result.data is not None
        assert result.data.background is True
        assert result.data.task_id == "task_abc123"
        assert result.data.status == "running"
        mocked_run.assert_awaited_once()
        assert mocked_run.await_args.kwargs["run_in_background"] is True

    @pytest.mark.asyncio
    async def test_agent_tool_returns_structured_agent_receipt(self):
        """AgentTool 应返回结构化 AgentReceipt，供 TUI 渲染子卡片。"""
        tool = AgentTool()
        args = AgentToolInput(
            description="Search config files",
            prompt="Find config files in the repo",
            subagent_type="Explore",
            run_in_background=True,
        )

        context = MagicMock()
        context.options = {
            "api_client": AsyncMock(),
            "tools": [],
            "model": "claude-sonnet-4-20250514",
            "cwd": "/tmp",
        }

        with patch(
            "codo.team.enhanced_agent.run_subagent_with_mode",
            new=AsyncMock(
                return_value={
                    "result": "Background task started: task_123",
                    "mode": "fresh",
                    "agent_id": "agent_42",
                    "task_id": "task_123",
                    "status": "running",
                    "is_background": True,
                    "total_tokens": 77,
                    "input_tokens": 50,
                    "output_tokens": 27,
                }
            ),
        ):
            result = await tool.call(args, context, lambda: True, None, None)

        assert result.error is None
        assert result.receipt is not None
        assert hasattr(tool_receipts, "AgentReceipt")
        assert isinstance(result.receipt, tool_receipts.AgentReceipt)
        assert result.receipt.agent_id == "agent_42"
        assert result.receipt.task_id == "task_123"
        assert result.receipt.background is True
        assert result.receipt.status == "running"
