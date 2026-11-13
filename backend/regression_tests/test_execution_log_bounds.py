from types import SimpleNamespace
import json

import pytest
from pydantic import ValidationError

from app.core.settings import Settings
from app.infrastructure.execution import execution_log
from app.infrastructure.execution.execution_log import ExecutionLog
from app.application.notifications.task_notify import build_task_section


def test_small_logs_preserve_existing_format_and_events():
    events = []
    log = ExecutionLog(max_bytes=512, emit_line=events.append)
    log.section('阶段')
    log.line('内容🙂')
    assert log.render() == '===============阶段===============\n内容🙂\n'
    assert events == ['===============阶段===============', '内容🙂']
    assert not log.truncated


def test_retained_memory_and_render_are_bounded_with_head_tail():
    log = ExecutionLog(max_bytes=512)
    log.line('START 任务信息')
    for _ in range(2000):
        log.line('中间内容🙂' * 20)
    log.line('END 最终错误')
    value = log.render()
    assert len(value.encode('utf-8')) <= 512
    assert 'START 任务信息' in value and 'END 最终错误' in value
    assert '截断' in value
    assert log.truncated and log.dropped_bytes > 100000
    assert log.retained_bytes <= 512
    assert log.render() == value


@pytest.mark.parametrize('limit', [1, 2, 3, 9, 64, 128])
def test_single_huge_line_utf8_and_tiny_limits(limit):
    log = ExecutionLog(max_bytes=limit)
    log.line('中文🙂' * 10000)
    data = log.render().encode('utf-8')
    assert len(data) <= limit
    assert data.decode('utf-8') == log.render()
    assert log.retained_bytes <= limit


def test_tree_limit_and_invalid_configuration():
    tree, cut = execution_log.bounded_text('文件🙂\n' * 100, 64)
    assert cut and len(tree.encode('utf-8')) <= 64
    for key in ('execution_log_max_bytes', 'execution_tree_max_bytes'):
        with pytest.raises(ValidationError):
            Settings(**{key: 0})


def test_notification_uses_structured_new_files_not_truncated_tree():
    task = SimpleNamespace(taskname='剧集', savepath='/剧集')
    execution = SimpleNamespace(
        status='success', tree_summary='…已截断…', message='',
        adapter_snapshot=json.dumps({'new_files': ['第01集.mp4', '第02集.mp4']}),
    )
    text, notify = build_task_section(task, execution)
    assert notify and '第01集.mp4' in text and '第02集.mp4' in text


def test_new_file_names_come_from_node_data_without_arrow_tags():
    from treelib import Tree
    from app.infrastructure.execution.task_executor import _new_files_from_tree
    tree = Tree()
    tree.create_node('剧', 'root')
    tree.create_node('原始名称', 'file', parent='root', data={'file_name_re': '新名称.mp4'})
    tree.create_node('目录', 'dir', parent='root', data={'is_dir': True})
    assert _new_files_from_tree(tree) == ['新名称.mp4']


def test_typed_execution_keeps_snapshot_in_presenter_and_share_notifications():
    from datetime import datetime
    from app.application.dto.task_run import TaskRunResult
    from app.application.queries.task_views import execution_view
    from app.application.generated_share_check import read_share_check
    result = TaskRunResult(id=1, task_id=2, status='success', started_at=datetime.now(),
                           adapter_snapshot={'execution_log': {'truncated': True},
                                             'generated_share_check': {'status': 'invalid', 'notify': True}})
    assert execution_view(result).adapter_snapshot['execution_log']['truncated']
    assert read_share_check(result.adapter_snapshot)['notify']

