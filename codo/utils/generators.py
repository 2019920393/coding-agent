"""
生成器工具

提供异步生成器的组合工具，用于工具编排。

核心功能：
- async_all: 并发执行多个异步生成器
- async_map: 映射异步生成器
- async_filter: 过滤异步生成器
"""

import asyncio
from typing import AsyncGenerator, TypeVar, Callable, List, Any, Optional
from collections import deque

T = TypeVar('T')
U = TypeVar('U')

async def async_all(*generators: AsyncGenerator[T, None]) -> AsyncGenerator[T, None]:
    """
    并发执行多个异步生成器，按完成顺序产出结果

    类似 Promise.all()，但用于异步生成器。
    所有生成器并发执行，结果按完成顺序产出。

    Args:
        *generators: 多个异步生成器

    Yields:
        各生成器产出的值（按完成顺序）

    Example:
        async def gen1():
            yield 1
            await asyncio.sleep(0.1)
            yield 2

        async def gen2():
            yield 3
            yield 4

        async for value in async_all(gen1(), gen2()):
            print(value)  # 可能输出: 1, 3, 4, 2
    """
    if not generators:
        return

    # [Workflow] 为每个生成器创建任务
    tasks = []
    queues = []

    for gen in generators:
        queue: asyncio.Queue = asyncio.Queue()
        queues.append(queue)

        async def consume(generator: AsyncGenerator[T, None], q: asyncio.Queue) -> None:
            """消费生成器，将结果放入队列"""
            try:
                async for item in generator:
                    await q.put(('value', item))
            except Exception as e:
                await q.put(('error', e))
            finally:
                await q.put(('done', None))

        task = asyncio.create_task(consume(gen, queue))
        tasks.append(task)

    # [Workflow] 从所有队列中读取结果
    active_queues = set(range(len(queues)))

    while active_queues:
        # 等待任意队列有数据
        done, pending = await asyncio.wait(
            [asyncio.create_task(queues[i].get()) for i in active_queues],
            return_when=asyncio.FIRST_COMPLETED
        )

        for task in done:
            result = task.result()
            msg_type, value = result

            if msg_type == 'value':
                yield value
            elif msg_type == 'error':
                raise value
            elif msg_type == 'done':
                # 找到对应的队列并移除
                for i in active_queues:
                    if queues[i].empty():
                        active_queues.discard(i)
                        break

    # [Workflow] 等待所有任务完成
    await asyncio.gather(*tasks, return_exceptions=True)

async def async_map(
    generator: AsyncGenerator[T, None],
    fn: Callable[[T], U]
) -> AsyncGenerator[U, None]:
    """
    映射异步生成器的值

    对生成器产出的每个值应用映射函数。

    Args:
        generator: 源异步生成器
        fn: 映射函数

    Yields:
        映射后的值

    Example:
        async def gen():
            yield 1
            yield 2
            yield 3

        async for value in async_map(gen(), lambda x: x * 2):
            print(value)  # 输出: 2, 4, 6
    """
    async for item in generator:
        yield fn(item)

async def async_filter(
    generator: AsyncGenerator[T, None],
    predicate: Callable[[T], bool]
) -> AsyncGenerator[T, None]:
    """
    过滤异步生成器的值

    只产出满足条件的值。

    Args:
        generator: 源异步生成器
        predicate: 过滤条件函数

    Yields:
        满足条件的值

    Example:
        async def gen():
            yield 1
            yield 2
            yield 3
            yield 4

        async for value in async_filter(gen(), lambda x: x % 2 == 0):
            print(value)  # 输出: 2, 4
    """
    async for item in generator:
        if predicate(item):
            yield item

async def async_collect(generator: AsyncGenerator[T, None]) -> List[T]:
    """
    收集异步生成器的所有值到列表

    Args:
        generator: 异步生成器

    Returns:
        所有值的列表

    Example:
        async def gen():
            yield 1
            yield 2
            yield 3

        result = await async_collect(gen())
        print(result)  # [1, 2, 3]
    """
    result = []
    async for item in generator:
        result.append(item)
    return result

async def async_race(*generators: AsyncGenerator[T, None]) -> AsyncGenerator[T, None]:
    """
    竞速执行多个异步生成器，产出最快的结果

    类似 Promise.race()，但用于异步生成器。
    只要有任意生成器产出值，就立即产出。

    Args:
        *generators: 多个异步生成器

    Yields:
        最快产出的值

    Example:
        async def slow():
            await asyncio.sleep(1)
            yield "slow"

        async def fast():
            yield "fast"

        async for value in async_race(slow(), fast()):
            print(value)  # 输出: "fast"
            break
    """
    if not generators:
        return

    # [Workflow] 创建任务队列
    pending_tasks = set()

    for gen in generators:
        task = asyncio.create_task(gen.__anext__())
        pending_tasks.add(task)

    # [Workflow] 等待最快的结果
    while pending_tasks:
        done, pending_tasks = await asyncio.wait(
            pending_tasks,
            return_when=asyncio.FIRST_COMPLETED
        )

        for task in done:
            try:
                value = task.result()
                yield value
            except StopAsyncIteration:
                # 生成器结束
                pass
            except Exception as e:
                raise e

class AsyncGeneratorPool:
    """
    异步生成器池

    管理多个异步生成器的并发执行，支持限制最大并发数。

    Attributes:
        max_concurrency: 最大并发数
        _active: 活跃任务集合
        _queue: 等待队列
    """

    def __init__(self, max_concurrency: int = 10):
        """
        初始化生成器池

        Args:
            max_concurrency: 最大并发数，默认 10
        """
        self.max_concurrency = max_concurrency
        self._active: set = set()
        self._queue: deque = deque()

    async def add(self, generator: AsyncGenerator[T, None]) -> AsyncGenerator[T, None]:
        """
        添加生成器到池中

        如果当前并发数未达上限，立即执行；否则加入等待队列。

        Args:
            generator: 异步生成器

        Yields:
            生成器产出的值
        """
        # [Workflow] 等待有空闲槽位
        while len(self._active) >= self.max_concurrency:
            await asyncio.sleep(0.01)

        # [Workflow] 执行生成器
        task_id = id(generator)
        self._active.add(task_id)

        try:
            async for item in generator:
                yield item
        finally:
            self._active.discard(task_id)

    @property
    def active_count(self) -> int:
        """当前活跃任务数"""
        return len(self._active)

    @property
    def is_full(self) -> bool:
        """是否已达最大并发数"""
        return len(self._active) >= self.max_concurrency
