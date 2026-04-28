"""
诊断日志系统

[Workflow]

功能：
1. 记录关键操作的开始/完成/失败事件
2. 记录性能指标（耗时、数据量等）
3. JSONL 格式输出到文件
4. 通过环境变量 CODO_DIAGNOSTICS_FILE 控制

设计原则：
- 静默失败：日志记录失败不应影响主流程
- 无 PII：不记录用户敏感信息
- 结构化：使用 JSON 格式便于分析
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

def log_diagnostics(
    level: str,
    event: str,
    data: Optional[Dict[str, Any]] = None
) -> None:
    """
    记录诊断日志到文件

    Args:
        level: 日志级别 (debug/info/warn/error)
        event: 事件名称 (如 git_status_started)
        data: 可选的额外数据字典

    [Workflow]
    1. 检查环境变量 CODO_DIAGNOSTICS_FILE
    2. 如果未设置，静默返回
    3. 构建日志条目（timestamp, level, event, data）
    4. 追加到日志文件（JSONL 格式）
    5. 如果失败，尝试创建目录后重试
    6. 如果仍失败，静默忽略
    """
    log_file = os.getenv('CODO_DIAGNOSTICS_FILE')
    if not log_file:
        return

    # 构建日志条目
    entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'level': level,
        'event': event,
        'data': data or {}
    }

    # 追加到日志文件
    try:
        with open(log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False) + '\n')
    except Exception:
        # 尝试创建目录
        try:
            Path(log_file).parent.mkdir(parents=True, exist_ok=True)
            with open(log_file, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except Exception:
            # 静默失败
            pass

def log_info(event: str, data: Optional[Dict[str, Any]] = None) -> None:
    """记录 info 级别日志"""
    log_diagnostics('info', event, data)

def log_error(event: str, data: Optional[Dict[str, Any]] = None) -> None:
    """记录 error 级别日志"""
    log_diagnostics('error', event, data)

def log_warn(event: str, data: Optional[Dict[str, Any]] = None) -> None:
    """记录 warn 级别日志"""
    log_diagnostics('warn', event, data)

def log_debug(event: str, data: Optional[Dict[str, Any]] = None) -> None:
    """记录 debug 级别日志"""
    log_diagnostics('debug', event, data)
