"""
诊断日志系统测试
"""

import os
import json
import tempfile
from pathlib import Path

from codo.utils.diagnostics import (
    log_diagnostics,
    log_info,
    log_error,
    log_warn,
    log_debug
)

def test_log_diagnostics_basic():
    """测试基本日志记录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = os.path.join(tmpdir, 'test.log')
        os.environ['CODO_DIAGNOSTICS_FILE'] = log_file

        try:
            log_diagnostics('info', 'test_event', {'key': 'value'})

            # 验证日志文件存在
            assert os.path.exists(log_file)

            # 读取并验证日志内容
            with open(log_file, 'r') as f:
                line = f.readline()
                entry = json.loads(line)

            assert entry['level'] == 'info'
            assert entry['event'] == 'test_event'
            assert entry['data']['key'] == 'value'
            assert 'timestamp' in entry

        finally:
            del os.environ['CODO_DIAGNOSTICS_FILE']

def test_log_diagnostics_no_env():
    """测试未设置环境变量时静默失败"""
    if 'CODO_DIAGNOSTICS_FILE' in os.environ:
        del os.environ['CODO_DIAGNOSTICS_FILE']

    # 应该不抛出异常
    log_diagnostics('info', 'test_event')

def test_log_diagnostics_auto_create_dir():
    """测试自动创建目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = os.path.join(tmpdir, 'subdir', 'test.log')
        os.environ['CODO_DIAGNOSTICS_FILE'] = log_file

        try:
            log_diagnostics('info', 'test_event')

            # 验证目录和文件都被创建
            assert os.path.exists(log_file)

        finally:
            del os.environ['CODO_DIAGNOSTICS_FILE']

def test_log_level_helpers():
    """测试日志级别辅助函数"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = os.path.join(tmpdir, 'test.log')
        os.environ['CODO_DIAGNOSTICS_FILE'] = log_file

        try:
            log_info('info_event')
            log_error('error_event')
            log_warn('warn_event')
            log_debug('debug_event')

            # 读取所有日志
            with open(log_file, 'r') as f:
                lines = f.readlines()

            assert len(lines) == 4

            entries = [json.loads(line) for line in lines]
            assert entries[0]['level'] == 'info'
            assert entries[1]['level'] == 'error'
            assert entries[2]['level'] == 'warn'
            assert entries[3]['level'] == 'debug'

        finally:
            del os.environ['CODO_DIAGNOSTICS_FILE']

def test_log_diagnostics_append():
    """测试追加模式"""
    with tempfile.TemporaryDirectory() as tmpdir:
        log_file = os.path.join(tmpdir, 'test.log')
        os.environ['CODO_DIAGNOSTICS_FILE'] = log_file

        try:
            log_info('event1')
            log_info('event2')
            log_info('event3')

            # 读取所有日志
            with open(log_file, 'r') as f:
                lines = f.readlines()

            assert len(lines) == 3

        finally:
            del os.environ['CODO_DIAGNOSTICS_FILE']
