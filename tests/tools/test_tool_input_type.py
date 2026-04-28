"""测试 SDK 在流式响应中 tool_use.input 的类型"""
import asyncio
from anthropic import AsyncAnthropic

async def test_tool_input_type():
    client = AsyncAnthropic(api_key="test-key")

    # 模拟一个简单的工具调用
    try:
        async with client.messages.stream(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1024,
            messages=[{"role": "user", "content": "List files in current directory"}],
            tools=[{
                "name": "bash",
                "description": "Execute bash command",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string"}
                    },
                    "required": ["command"]
                }
            }]
        ) as stream:
            async for event in stream:
                if event.type == "content_block_start":
                    if hasattr(event, 'content_block') and event.content_block.type == "tool_use":
                        print(f"content_block_start - input type: {type(event.content_block.input)}")
                        print(f"content_block_start - input value: {event.content_block.input}")

                elif event.type == "content_block_delta":
                    if hasattr(event, 'delta') and event.delta.type == "input_json_delta":
                        print(f"input_json_delta - partial_json type: {type(event.delta.partial_json)}")
                        print(f"input_json_delta - partial_json value: {event.delta.partial_json}")

            final_message = await stream.get_final_message()
            for block in final_message.content:
                if block.type == "tool_use":
                    print(f"\nfinal_message - input type: {type(block.input)}")
                    print(f"final_message - input value: {block.input}")
                    print(f"final_message - input is dict: {isinstance(block.input, dict)}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_tool_input_type())
