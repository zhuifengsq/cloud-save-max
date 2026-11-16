from types import SimpleNamespace

import pytest

from app.application.drive_account_auth_sessions import create_auth_session, get_auth_session, delete_auth_session
from app.application.use_cases.drive_account_auth import DriveAccountAuthUseCase


@pytest.mark.parametrize('method', ['captcha', 'sms', 'qrcode'])
def test_completed_challenge_retries_persistence_without_repeating_cloud_write(method):
    calls = []
    adapter = SimpleNamespace(submit_captcha=lambda code: calls.append('captcha'),
        submit_sms=lambda code: calls.append('sms'), export_runtime_config=lambda: {'cookie': 'candidate'})
    provider = SimpleNamespace(query_qrcode_status=lambda t, ck: calls.append('qrcode') or
        {'success': True, 'data': {'status': 'CONFIRMED', 'refresh_token': 'candidate'}})
    account = SimpleNamespace(id=42)
    gateway = SimpleNamespace(prepare=lambda db, identity, version=None: 7,
        probe=lambda *args, **kwargs: account)
    uc = DriveAccountAuthUseCase(provider=provider, accounts=gateway)
    session = create_auth_session(account_id=42, drive_type='test', method=method,
        adapter={} if method == 'qrcode' else adapter, payload={}, row_version=7)
    def persist_failure(account):
        raise RuntimeError('database unavailable')
    def finish(persist):
        if method == 'qrcode':
            return uc.poll_qrcode(None, session.session_id, persist=persist)
        return uc.submit_code(None, session.session_id, '123456', persist=persist)
    try:
        with pytest.raises(RuntimeError, match='database unavailable'):
            finish(persist_failure)
        assert get_auth_session(session.session_id) is session
        result = finish(lambda account: None)
        assert result.account is account
        assert calls == [method]
        assert get_auth_session(session.session_id) is None
    finally:
        delete_auth_session(session.session_id)


def test_telegram_auth_uses_injected_workflow_and_commits_before_completion():
    from app.application.telegram_bot.account_actions import TelegramAccountActions
    events = []
    session = SimpleNamespace(account_id=42, drive_type='aliyun', method='qrcode', session_id='id', payload={})
    account = SimpleNamespace(id=42)
    class Workflow:
        def poll_qrcode(self, db, sid, *, persist):
            assert sid == 'id'
            events.append('challenge')
            persist(account)
            events.append('complete')
            return SimpleNamespace(session=session, account=account)
    db = SimpleNamespace(commit=lambda: events.append('commit'), refresh=lambda row: events.append('refresh'))
    import app.application.telegram_bot.account_actions as module
    original = module.serialize_drive_account
    module.serialize_drive_account = lambda row: {'id': row.id}
    try:
        result = TelegramAccountActions(db, auth=Workflow()).poll_account_auth_qrcode('id')
    finally:
        module.serialize_drive_account = original
    assert result == {'done': True, 'account': {'id': 42}}
    assert events == ['challenge', 'commit', 'complete', 'refresh']

@pytest.mark.parametrize('cleanup', ['delete', 'expire', 'purge'])
def test_auth_session_owns_retained_adapter_until_cleanup(monkeypatch, cleanup):
    closed = []
    retained = SimpleNamespace(close=lambda: closed.append('retained'))
    original = SimpleNamespace(retain=lambda: retained, close=lambda: closed.append('original'))
    session = create_auth_session(account_id=42, drive_type='quark', method='sms', adapter=original, payload={})
    try:
        assert session.adapter is retained
        if cleanup == 'delete':
            delete_auth_session(session.session_id)
        else:
            session.expires_at = 0
            if cleanup == 'expire':
                assert get_auth_session(session.session_id) is None
            else:
                another = create_auth_session(account_id=43, drive_type='aliyun', method='qrcode', adapter={}, payload={})
                delete_auth_session(another.session_id)
        delete_auth_session(session.session_id)
        assert closed == ['retained']
    finally:
        delete_auth_session(session.session_id)


def test_sms_export_failure_does_not_resubmit_accepted_code():
    calls = []
    def export():
        calls.append('export')
        if calls.count('export') == 1:
            raise RuntimeError('config temporarily unavailable')
        return {'cookie': 'candidate'}
    adapter = SimpleNamespace(submit_sms=lambda code: calls.append('submit'), export_runtime_config=export)
    account = SimpleNamespace(id=42)
    gateway = SimpleNamespace(prepare=lambda *args: 7, probe=lambda *args, **kwargs: account)
    uc = DriveAccountAuthUseCase(provider=SimpleNamespace(), accounts=gateway)
    session = create_auth_session(account_id=42, drive_type='quark', method='sms', adapter=adapter, payload={})
    try:
        with pytest.raises(RuntimeError, match='config temporarily unavailable'):
            uc.submit_code(None, session.session_id, '123456', persist=lambda row: None)
        uc.submit_code(None, session.session_id, '123456', persist=lambda row: None)
        assert calls == ['submit', 'export', 'export']
    finally:
        delete_auth_session(session.session_id)

@pytest.mark.parametrize('cleanup', ['delete', 'expire'])
def test_real_lease_challenge_survives_request_end_and_cache_retirement(cleanup):
    import json
    from app.application import drive_accounts
    from app.application.drive_account_operation import AccountOperation
    from app.core.drive_auth import DriveAuthRequired
    from app.infrastructure.drive.lifecycle import AdapterResource, adapter_scope

    class Challenge:
        def __init__(self):
            self.closed = False
        def init(self):
            raise DriveAuthRequired(method='captcha', message='captcha required', adapter=self)
        def submit_captcha(self, code):
            assert not self.closed
            raise DriveAuthRequired(method='sms', message='sms required', adapter=self)
        def close(self):
            self.closed = True

    raw = Challenge()
    resource = AdapterResource(raw)
    sid = None
    next_sid = None
    try:
        with adapter_scope():
            lease = resource.acquire()
            try:
                lease.init()
            except DriveAuthRequired as exc:
                error = drive_accounts._auth_required(AccountOperation(42, 7, 'test', 'cloud189', {}, ''), lease, exc)
                sid = json.loads(error.detail)['session_id']
            resource.retire()
        assert sid is not None
        assert not raw.closed
        uc = DriveAccountAuthUseCase(provider=SimpleNamespace(),
            accounts=SimpleNamespace(prepare=lambda *args: 7))
        result = uc.submit_code(None, sid, '1234', persist=lambda account: None)
        next_sid = result.session.session_id
        assert result.session.method == 'sms'
        assert get_auth_session(sid) is None
        assert not raw.closed
        if cleanup == 'expire':
            result.session.expires_at = 0
            assert get_auth_session(next_sid) is None
        else:
            delete_auth_session(next_sid)
        assert raw.closed
    finally:
        if sid:
            delete_auth_session(sid)
        if next_sid:
            delete_auth_session(next_sid)
        resource.retire()


def test_sms_waiter_rechecks_session_after_another_operation_completed(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.core.errors import ApiError

    events = []
    uc = DriveAccountAuthUseCase(provider=SimpleNamespace(),
        accounts=SimpleNamespace(prepare=lambda *args: events.append('prepare')))
    session = create_auth_session(account_id=42, drive_type='cloud189', method='sms',
        adapter=SimpleNamespace(send_sms=lambda: events.append('send')), payload={})
    looked_up = Event()
    status = uc.status

    def observe_status(*args):
        value = status(*args)
        looked_up.set()
        return value

    monkeypatch.setattr(uc, 'status', observe_status)
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        with session.operation_lock:
            future = executor.submit(uc.send_sms, None, session.session_id)
            assert looked_up.wait(timeout=2)
            delete_auth_session(session.session_id)
        with pytest.raises(ApiError) as error:
            future.result(timeout=2)
        assert error.value.code == 'DRIVE_ACCOUNT_AUTH_SESSION_NOT_FOUND'
        assert events == []
    finally:
        executor.shutdown(wait=True)
        delete_auth_session(session.session_id)


def test_app_shutdown_releases_interactive_auth_sessions(monkeypatch):
    from unittest.mock import MagicMock
    from fastapi.testclient import TestClient
    from app import main
    from app.application import task_executions
    from app.application import drive_account_auth_sessions as sessions
    from app.infrastructure.drive.lifecycle import AdapterResource
    from app.infrastructure.drive.factory import AdapterFactory

    events = []
    raw = SimpleNamespace(close=lambda: events.append('closed'))
    resource = AdapterResource(raw)
    lease = resource.acquire(track_scope=False)
    session = sessions.create_auth_session(account_id=42, drive_type='fake', method='sms',
        adapter=lease, payload={})
    lease.close()
    resource.retire()
    monkeypatch.setattr(main, 'backup_before_migrate', lambda: None)
    monkeypatch.setattr(main, '_run_db_migrations_if_needed', lambda: None)
    monkeypatch.setattr(main, '_ensure_proxy_image_cache_dir', lambda: None)
    monkeypatch.setattr(main, 'SessionLocal', MagicMock())
    monkeypatch.setattr(main, 'ensure_permissions_and_roles', lambda db: None)
    monkeypatch.setattr(task_executions, 'prune_all_task_executions', lambda db: 0)
    monkeypatch.setattr(main, 'login_failure_audit_service', lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(main.telegram_bot_manager, 'start', lambda: None)
    monkeypatch.setattr(main.telegram_bot_manager, 'shutdown', lambda: None)
    monkeypatch.setattr(main.task_scheduler_manager, 'start', lambda: None)
    monkeypatch.setattr(main.task_scheduler_manager, 'shutdown', lambda: None)
    try:
        with TestClient(main.create_app()):
            assert sessions.get_auth_session(session.session_id) is session
        assert sessions.get_auth_session(session.session_id) is None
        assert events == ['closed']
    finally:
        sessions.delete_auth_session(session.session_id)
        AdapterFactory.startup()
        sessions.startup_auth_sessions()


def test_concurrent_challenge_transitions_do_not_deadlock_at_capacity(monkeypatch):
    import threading
    from app.application import drive_account_auth_sessions as sessions
    from app.core.drive_auth import DriveAuthRequired

    monkeypatch.setattr(sessions, '_SESSIONS', {})
    monkeypatch.setattr(sessions, '_MAX_SESSIONS', 2)
    entered = threading.Barrier(2)
    retired_b = threading.Event()
    results = []
    class Challenge:
        def __init__(self, wait): self.wait = wait
        def submit_captcha(self, code):
            entered.wait(timeout=2)
            if self.wait:
                assert retired_b.wait(timeout=2)
            raise DriveAuthRequired(method='sms', message='next challenge', adapter=self)
    b = sessions.create_auth_session(account_id=2, drive_type='fake', method='captcha', adapter=Challenge(True), payload={})
    a = sessions.create_auth_session(account_id=1, drive_type='fake', method='captcha', adapter=Challenge(False), payload={})
    close_session = sessions._close_session
    def close(item):
        if item is b:
            retired_b.set()
        close_session(item)
    monkeypatch.setattr(sessions, '_close_session', close)
    uc = DriveAccountAuthUseCase(provider=SimpleNamespace(), accounts=SimpleNamespace(prepare=lambda *a: 7))
    def submit(sid):
        try:
            results.append(uc.submit_code(None, sid, 'fake', persist=lambda _: None))
        except Exception as error:
            results.append(error)
    threads = [threading.Thread(target=submit, args=(session.session_id,), daemon=True) for session in (a, b)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(timeout=2)
    assert not any(thread.is_alive() for thread in threads), 'session transitions deadlocked during eviction'
    assert len(results) == 2 and all(not isinstance(item, Exception) for item in results)
    for sid in list(sessions._SESSIONS): sessions.delete_auth_session(sid)


def test_retired_session_closes_transport_after_active_operation_finishes():
    import threading
    from app.application import drive_account_auth_sessions as sessions
    from app.infrastructure.drive.lifecycle import AdapterResource

    events = []
    started, finish = threading.Event(), threading.Event()
    resource = AdapterResource(SimpleNamespace(close=lambda: events.append('closed')))
    lease = resource.acquire(track_scope=False)
    session = sessions.create_auth_session(account_id=42, drive_type='fake', method='sms', adapter=lease, payload={})
    lease.close()
    resource.retire()
    def operation():
        with session.operation():
            started.set()
            assert finish.wait(timeout=3)
    thread = threading.Thread(target=operation, daemon=True)
    thread.start()
    assert started.wait(timeout=2)
    try:
        sessions.delete_auth_session(session.session_id)
        assert sessions.get_auth_session(session.session_id) is None
        assert events == []
    finally:
        finish.set()
        thread.join(timeout=2)
    assert not thread.is_alive()
    assert events == ['closed']


def test_retirement_racing_operation_exit_does_not_lose_transport_cleanup(monkeypatch):
    import threading
    from app.application import drive_account_auth_sessions as sessions
    from app.infrastructure.drive.lifecycle import AdapterResource

    monkeypatch.setattr(sessions, '_SESSIONS', {})
    read_started, deleted = threading.Event(), threading.Event()
    events = []
    resource = AdapterResource(SimpleNamespace(close=lambda: events.append('closed')))
    lease = resource.acquire(track_scope=False)
    session = sessions.create_auth_session(account_id=42, drive_type='fake', method='sms', adapter=lease, payload={})
    lease.close()
    resource.retire()
    class ControlledSession(type(session)):
        def __getattribute__(self, name):
            value = super().__getattribute__(name)
            if name == 'retired' and value is False:
                read_started.set()
                # With synchronized state, deletion waits for this read to finish;
                # without it deletion completes and the stale False loses cleanup.
                deleted.wait(timeout=0.3)
            return value
    session.__class__ = ControlledSession
    def retire():
        assert read_started.wait(timeout=2)
        sessions.delete_auth_session(session.session_id)
        deleted.set()
    thread = threading.Thread(target=retire, daemon=True)
    thread.start()
    with session.operation():
        pass
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert events == ['closed']


def test_shutdown_rejects_late_auth_session_creation():
    from app.application import drive_account_auth_sessions as sessions
    from app.core.errors import ApiError
    from app.infrastructure.drive.lifecycle import AdapterResource

    events = []
    resource = AdapterResource(SimpleNamespace(close=lambda: events.append('closed')))
    lease = resource.acquire(track_scope=False)
    resource.retire()
    try:
        sessions.clear_auth_sessions()
        # Exercise the public application shutdown path once it is available.
        shutdown = getattr(sessions, 'shutdown_auth_sessions', sessions.clear_auth_sessions)
        shutdown()
        with pytest.raises(ApiError) as caught:
            sessions.create_auth_session(account_id=42, drive_type='fake', method='sms', adapter=lease, payload={})
        assert caught.value.http_status == 503
    finally:
        sessions.clear_auth_sessions()
        startup = getattr(sessions, 'startup_auth_sessions', lambda: None)
        startup()
        lease.close()
    assert events == ['closed']
