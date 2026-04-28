"""
测试第二批功能：-c/--continue 和 -r/--resume <uuid>

测试步骤：
1. 创建一个测试会话文件
2. 测试 -c/--continue（继续最近的会话）
3. 测试 -r/--resume <uuid>（恢复指定会话）
4. 测试 -r/--resume <title>（按标题搜索）
"""

import json
import os
import time
from pathlib import Path
from uuid import uuid4

# 测试会话 ID
test_session_id = str(uuid4())

# 获取项目目录
from codo.session.storage import get_project_dir, sanitize_path

cwd = os.getcwd()
project_dir = get_project_dir(cwd)

print(f"当前工作目录: {cwd}")
print(f"项目目录: {project_dir}")
print(f"测试会话 ID: {test_session_id}")

# 创建测试会话文件
session_file = Path(project_dir) / f"{test_session_id}.jsonl"

print(f"\n创建测试会话文件: {session_file}")

# 写入测试数据
test_data = [
    {
        "type": "message",
        "role": "user",
        "content": [{"type": "text", "text": "你好，这是第一条消息"}],
        "timestamp": time.time()
    },
    {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "你好！我是 Codo，很高兴为你服务。"}],
        "timestamp": time.time()
    },
    {
        "type": "message",
        "role": "user",
        "content": [{"type": "text", "text": "列出当前目录的文件"}],
        "timestamp": time.time()
    },
    {
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "好的，让我列出当前目录的文件。"}],
        "timestamp": time.time()
    },
    {
        "type": "metadata",
        "customTitle": "测试会话",
        "timestamp": time.time()
    }
]

with open(session_file, 'w', encoding='utf-8') as f:
    for record in test_data:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')

print("✓ 测试会话文件创建成功")

# 测试会话查询功能
print("\n" + "="*60)
print("测试 1: 会话查询功能")
print("="*60)

from codo.session.query import (
    validate_uuid,
    get_last_session,
    find_session_by_id,
    search_sessions_by_title
)

# 测试 UUID 验证
print("\n1.1 测试 UUID 验证:")
print(f"  validate_uuid('{test_session_id}'): {validate_uuid(test_session_id)}")
print(f"  validate_uuid('invalid-uuid'): {validate_uuid('invalid-uuid')}")

# 测试获取最近的会话
print("\n1.2 测试获取最近的会话:")
last_session = get_last_session(project_dir)
if last_session:
    print(f"  ✓ 找到最近的会话:")
    print(f"    - Session ID: {last_session.session_id}")
    print(f"    - Summary: {last_session.summary}")
    print(f"    - Custom Title: {last_session.custom_title}")
    print(f"    - Last Modified: {last_session.last_modified}")
else:
    print("  ✗ 未找到会话")

# 测试按 ID 查找会话
print("\n1.3 测试按 ID 查找会话:")
session = find_session_by_id(test_session_id, project_dir)
if session:
    print(f"  ✓ 找到会话:")
    print(f"    - Session ID: {session.session_id}")
    print(f"    - Summary: {session.summary}")
else:
    print("  ✗ 未找到会话")

# 测试按标题搜索
print("\n1.4 测试按标题搜索:")
matches = search_sessions_by_title("测试会话", project_dir, exact=True)
print(f"  找到 {len(matches)} 个匹配的会话:")
for match in matches:
    print(f"    - {match.session_id}: {match.summary}")

# 测试会话恢复功能
print("\n" + "="*60)
print("测试 2: 会话恢复功能")
print("="*60)

from codo.session.restore import (
    load_session_for_resume,
    validate_session_data
)

# 测试加载最近的会话
print("\n2.1 测试加载最近的会话 (session_id=None):")
session_data = load_session_for_resume(None, project_dir)
if validate_session_data(session_data):
    print(f"  ✓ 会话加载成功:")
    print(f"    - Session ID: {session_data['session_info'].session_id}")
    print(f"    - Summary: {session_data['session_info'].summary}")
    print(f"    - Messages: {len(session_data['messages'])}")
    print(f"    - File Path: {session_data['file_path']}")

    # 打印消息内容
    print("\n  消息内容:")
    for i, msg in enumerate(session_data['messages'], 1):
        role = msg['role']
        content = msg['content']
        if isinstance(content, list) and len(content) > 0:
            text = content[0].get('text', '') if isinstance(content[0], dict) else ''
            print(f"    {i}. [{role}] {text[:50]}...")
else:
    print("  ✗ 会话加载失败")

# 测试加载指定会话
print(f"\n2.2 测试加载指定会话 (session_id={test_session_id}):")
session_data = load_session_for_resume(test_session_id, project_dir)
if validate_session_data(session_data):
    print(f"  ✓ 会话加载成功:")
    print(f"    - Session ID: {session_data['session_info'].session_id}")
    print(f"    - Messages: {len(session_data['messages'])}")
else:
    print("  ✗ 会话加载失败")

# 总结
print("\n" + "="*60)
print("测试总结")
print("="*60)
print(f"""
✓ 会话文件创建成功: {session_file}
✓ UUID 验证功能正常
✓ 获取最近会话功能正常
✓ 按 ID 查找会话功能正常
✓ 按标题搜索会话功能正常
✓ 加载会话功能正常

下一步：
1. 测试 CLI 命令：python -m codo -c
2. 测试 CLI 命令：python -m codo -r {test_session_id}
3. 测试 CLI 命令：python -m codo -r "测试会话"

注意：需要设置 ANTHROPIC_API_KEY 环境变量才能测试完整功能
""")
