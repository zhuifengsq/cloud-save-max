from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from sqlalchemy.exc import OperationalError

from app.application import api_search_sources as api, resource_search as search, search_plugins as plugins
from app.core.errors import ApiError


def batch(monkeypatch, effects):
    rows = [SimpleNamespace(config_json='{}') for _ in effects]
    monkeypatch.setattr(api, 'resource_search_sources_repo', lambda: SimpleNamespace(list_enabled_api_sources=lambda *a, **k: rows))
    def run(*args):
        value = effects.pop(0)
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(api, 'search_api_source', run)
    return api.search_enabled_api_sources_result(None, 'movie')


def test_source_successful_empty_is_success(monkeypatch):
    result = batch(monkeypatch, [[]])
    assert result.sources == result.succeeded == 1
    assert result.items == []


def test_source_partial_and_timeout_counts(monkeypatch):
    result = batch(monkeypatch, [[], requests.Timeout('secret'), requests.ConnectionError('secret')])
    assert (result.sources, result.succeeded, result.timed_out) == (3, 1, 1)
    assert len(result.warnings) == 2
    assert 'secret' not in str(result.warnings)


@pytest.mark.parametrize('error', [OperationalError('select secret', {}, Exception()), RuntimeError('bug'), ApiError('EXISTING', 'safe', 409)])
def test_source_database_program_and_api_errors_propagate(monkeypatch, error):
    with pytest.raises(type(error)):
        batch(monkeypatch, [error])


@pytest.mark.parametrize('effects,status,code', [
    ([requests.ConnectionError('secret')], 502, 'SEARCH_UPSTREAM_FAILED'),
    ([requests.Timeout('secret')], 504, 'SEARCH_UPSTREAM_TIMEOUT'),
    ([requests.Timeout('secret'), requests.ConnectionError('secret')], 502, 'SEARCH_UPSTREAM_FAILED'),
])
def test_all_sources_failure_is_not_empty_success(monkeypatch, effects, status, code):
    result = batch(monkeypatch, effects)
    monkeypatch.setattr(api, 'search_enabled_api_sources_result', lambda *a: result)
    from app.application.search_results import SearchBatch
    monkeypatch.setattr(plugins, 'search_enabled_plugins_result', lambda *a: SearchBatch())
    monkeypatch.setattr(search, '_update_search_cache_ttl', lambda: None)
    search.cache_clear()
    with pytest.raises(ApiError) as exc:
        search.fetch_task_suggestions_result(None, 'movie', 0)
    assert (exc.value.http_status, exc.value.code) == (status, code)
    assert 'secret' not in str(exc.value)


def test_no_sources_returns_explicit_message(monkeypatch):
    from app.application.search_results import SearchBatch
    monkeypatch.setattr(api, 'search_enabled_api_sources_result', lambda *a: SearchBatch())
    monkeypatch.setattr(plugins, 'search_enabled_plugins_result', lambda *a: SearchBatch())
    monkeypatch.setattr(search, '_update_search_cache_ttl', lambda: None)
    search.cache_clear()
    result = search.fetch_task_suggestions_result(None, 'movie', 0)
    assert result.items == []
    assert result.message == '未启用搜索源'


def test_cache_settings_database_failure_propagates(monkeypatch):
    from app.application import container
    from contextlib import contextmanager
    @contextmanager
    def broken():
        raise OperationalError('secret', {}, Exception())
        yield
    monkeypatch.setattr(container, 'db_session', broken)
    with pytest.raises(OperationalError):
        search._get_search_cache_ttl()

@pytest.mark.parametrize('partial', [False, True])
def test_successful_empty_does_not_require_drive_account(monkeypatch, partial):
    from app.application.search_results import SearchBatch
    monkeypatch.setattr(api, 'search_enabled_api_sources_result', lambda *a: SearchBatch(sources=2 if partial else 1, succeeded=1, warnings=['API 搜索源暂不可用'] if partial else []))
    monkeypatch.setattr(plugins, 'search_enabled_plugins_result', lambda *a: SearchBatch())
    monkeypatch.setattr(search, '_update_search_cache_ttl', lambda: None)
    search.cache_clear()
    result = search.fetch_task_suggestions_result(None, 'movie', 0)
    assert result.items == []
    assert result.message == '未找到结果'
    assert result.partial is partial


@pytest.mark.parametrize('error,status,code', [
    (ApiError('EXISTING', 'safe', 409), 409, 'EXISTING'),
    (ApiError('SEARCH_UPSTREAM_FAILED', 'safe', 502), 502, 'SEARCH_UPSTREAM_FAILED'),
    (ApiError('SEARCH_UPSTREAM_TIMEOUT', 'safe', 504), 504, 'SEARCH_UPSTREAM_TIMEOUT'),
    (OperationalError('secret', {}, Exception()), 500, 'INTERNAL_ERROR'),
    (RuntimeError('secret'), 500, 'INTERNAL_ERROR'),
])
def test_suggestions_http_error_contract(monkeypatch, error, status, code):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes import tasks
    from app.core.exception_handlers import api_error_handler, http_error_handler
    def fail(*a, **k):
        raise error
    monkeypatch.setattr(search, 'fetch_task_suggestions_result', fail)
    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(Exception, http_error_handler)
    app.dependency_overrides[tasks.get_db] = lambda: None
    app.add_api_route('/suggestions', tasks.get_task_suggestions)
    response = TestClient(app, raise_server_exceptions=False).get('/suggestions?q=movie')
    assert response.status_code == status
    assert response.json()['code'] == code
    assert 'secret' not in response.text


@pytest.mark.parametrize('message,partial', [('未启用搜索源', False), ('未找到结果', False), ('未找到结果', True)])
def test_suggestions_http_success_contract(monkeypatch, message, partial):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes import tasks
    from app.application.search_results import SuggestionResult
    monkeypatch.setattr(search, 'fetch_task_suggestions_result', lambda *a, **k: SuggestionResult(message=message, partial=partial, warnings=['safe'] if partial else []))
    app = FastAPI()
    app.dependency_overrides[tasks.get_db] = lambda: None
    app.add_api_route('/suggestions', tasks.get_task_suggestions)
    response = TestClient(app).get('/suggestions?q=movie')
    assert response.status_code == 200
    assert response.json() == {'success': True, 'data': [], 'message': message, 'partial': partial, 'warnings': ['safe'] if partial else []}

@pytest.mark.parametrize('error', [OperationalError('secret', {}, Exception()), RuntimeError('secret'), ApiError('EXISTING', 'safe', 409)])
def test_plugin_program_database_api_failure_propagates(error):
    def fail(keyword):
        raise error
    plugin = SimpleNamespace(key='demo', search=fail)
    with pytest.raises(type(error)):
        plugins._run_search(plugin, 'movie')


@pytest.mark.parametrize('error,timed_out', [(requests.Timeout('secret'), 1), (requests.ConnectionError('secret'), 0)])
def test_plugin_failed_source_is_counted(monkeypatch, error, timed_out):
    def fail(keyword):
        raise error
    plugin = SimpleNamespace(key='demo', search=fail, timeout_seconds=1)
    discovered = [{'key': 'demo', 'plugin': plugin, 'status': 'ready'}]
    monkeypatch.setattr(plugins, 'discover_search_plugins', lambda **k: discovered)
    monkeypatch.setattr(plugins, '_ensure_rows', lambda *a: {'plugin:demo': SimpleNamespace(enabled=True)})
    result = plugins.search_enabled_plugins_result(None, 'movie')
    assert (result.sources, result.succeeded, result.timed_out) == (1, 0, timed_out)
    assert 'secret' not in str(result.warnings)


def test_enabled_broken_plugin_counts_as_failure(monkeypatch):
    monkeypatch.setattr(plugins, 'discover_search_plugins', lambda **k: [{'key': 'demo', 'plugin': None, 'status': 'error'}])
    monkeypatch.setattr(plugins, '_ensure_rows', lambda *a: {'plugin:demo': SimpleNamespace(enabled=True)})
    result = plugins.search_enabled_plugins_result(None, 'movie')
    assert result.sources == 1
    assert result.succeeded == 0
    assert result.warnings == ['搜索插件加载失败']

def test_partial_results_keep_items_and_legacy_tuple(monkeypatch):
    from app.application.search_results import SearchBatch
    item = {'taskname': 'movie', 'shareurl': 'https://pan.quark.cn/s/demo'}
    monkeypatch.setattr(api, 'search_enabled_api_sources_result', lambda *a: SearchBatch(items=[item.copy()], sources=2, succeeded=1, warnings=['API 搜索源暂不可用']))
    monkeypatch.setattr(plugins, 'search_enabled_plugins_result', lambda *a: SearchBatch())
    monkeypatch.setattr(search, '_update_search_cache_ttl', lambda: None)
    monkeypatch.setattr(search, 'drive_accounts_repo', lambda: SimpleNamespace(list_active_drive_types=lambda *a: ['quark']))
    monkeypatch.setattr(search, 'list_invalid_shareurls', lambda *a, **k: [])
    search.cache_clear()
    result = search.fetch_task_suggestions_result(None, 'movie', 0)
    assert result.items == [item]
    assert result.partial
    assert result.warnings == ['API 搜索源暂不可用']
    legacy = search.fetch_task_suggestions(None, 'movie', 0)
    assert isinstance(legacy, tuple)
    assert legacy == ([item], False, None)

def test_plugin_internal_value_error_is_not_upstream_failure():
    def fail(keyword):
        return int('invalid internal value')
    with pytest.raises(ValueError):
        plugins._run_search(SimpleNamespace(key='demo', search=fail), 'movie')


def test_plugin_invalid_response_is_validation_failure():
    items, error = plugins._run_search(SimpleNamespace(key='demo', search=lambda k: {}), 'movie')
    assert items == []
    assert error == '搜索插件暂不可用'
