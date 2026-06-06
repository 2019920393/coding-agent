"""
测试 400 错误诊断脚本

用于重现和调试 "Improperly formed request" 错误
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from codo.query_engine import QueryEngine

# 启用详细日志
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)

logger = logging.getLogger(__name__)


async def test_simple_query():
    """测试最简单的查询"""
    # 从环境变量获取 API 密钥
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("未设置 ANTHROPIC_API_KEY 环境变量")
        return

    logger.info("创建 QueryEngine...")
    engine = QueryEngine(
        api_key=api_key,
        cwd=str(project_root),
        verbose=True,
        model="claude-sonnet-4-20250514",
    )

    logger.info("刷新工具列表...")
    await engine.refresh_mcp_tools()

    logger.info("发送简单查询...")
    try:
        async for event in engine.submit_message_stream("Hello, can you help me?"):
            event_type = event.get("type", "unknown")
            logger.debug(f"事件: {event_type}")

            if event_type == "error":
                logger.error(f"收到错误: {event}")
            elif event_type == "text_delta":
                print(event.get("delta", {}).get("text", ""), end="", flush=True)

        print()  # 换行
        logger.info("查询完成")
    except Exception as e:
        logger.exception(f"查询失败: {e}")


async def test_empty_messages():
    """测试空消息列表"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.error("未设置 ANTHROPIC_API_KEY 环境变量")
        return

    logger.info("测试空消息列表...")
    engine = QueryEngine(
        api_key=api_key,
        cwd=str(project_root),
        verbose=True,
        model="claude-sonnet-4-20250514",
        initial_messages=[],  # 空消息
    )

    try:
        async for event in engine.submit_message_stream(""):
            logger.debug(f"事件: {event.get('type')}")
    except Exception as e:
        logger.error(f"空消息测试错误 (预期): {e}")


async def main():
    """主函数"""
    logger.info("=" * 60)
    logger.info("开始 400 错误诊断测试")
    logger.info("=" * 60)

    # 测试 1: 简单查询
    logger.info("\n测试 1: 简单查询")
    await test_simple_query()

    # 测试 2: 空消息
    logger.info("\n测试 2: 空消息列表")
    await test_empty_messages()

    logger.info("\n" + "=" * 60)
    logger.info("测试完成")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
