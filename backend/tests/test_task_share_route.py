"""Transport contract; use-case tests cover SQLite persistence and cloud calls."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from starlette.requests import Request

from app.api.routes.tasks import post_create_task_share
from app.application.dto.task_share import TaskShareResult
from app.core.errors import ApiError


def test_route_passes_actor_context_and_preserves_share_response():
    captured = []
    result = TaskShareResult(
        share_url='https://pan.quark.cn/s/result', share_id='share-1', title='测试剧',
        drive_type='quark', account_name='夸克1', passcode='abcd', expires_in_days=7,
        saved_at='2026-11-13T00:00:00+08:00',
    )
    def execute(command):
        captured.append(command)
        return result
    request = Request({'type': 'http', 'method': 'POST', 'path': '/api/tasks/7/share',
                       'headers': [(b'user-agent', b'route-test')], 'client': ('127.0.0.1', 1)})
    db = object()
    with patch('app.api.routes.tasks.create_task_share_use_case',
               return_value=SimpleNamespace(execute=execute)) as factory:
        response = post_create_task_share(request, 7, SimpleNamespace(user=SimpleNamespace(id=11)), db)
    factory.assert_called_once_with(db)
    command = captured[0]
    assert (command.task_id, command.actor_user_id, command.ip, command.user_agent) == (7, 11, '127.0.0.1', 'route-test')
    assert response.share_url == result.share_url
    assert response.passcode == 'abcd' and response.saved_at == result.saved_at


def test_route_preserves_use_case_failure_without_local_commit():
    error = ApiError('TASK_SHARE_UNSUPPORTED', '不支持', 400)
    def execute(command):
        raise error
    request = Request({'type': 'http', 'method': 'POST', 'path': '/api/tasks/7/share', 'headers': []})
    with patch('app.api.routes.tasks.create_task_share_use_case', return_value=SimpleNamespace(execute=execute)):
        with pytest.raises(ApiError) as caught:
            post_create_task_share(request, 7, SimpleNamespace(user=SimpleNamespace(id=11)), object())
    assert caught.value is error
