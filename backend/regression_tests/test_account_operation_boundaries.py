import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.application import drive_accounts as service
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.drive_account import DriveAccount
import app.infrastructure.persistence.models


@pytest.fixture
def db(tmp_path):
    engine = create_engine('sqlite:///' + (tmp_path / 'operations.db').as_posix())
    Base.metadata.create_all(engine)
    with Session(engine, autoflush=False) as session:
        row = service.create_drive_account(session, name='test', drive_type='quark', config={'cookie': 'fake-old'})
        row.probe_fail_count = 2
        row.profile_json = json.dumps({'nickname': 'known'})
        session.commit()
        yield session, row.id
    engine.dispose()


def adapter_for(db, events, *, profile_error=False, init_result=True, init_error=False):
    def record(stage):
        events.append((stage, db.in_transaction()))
    def init():
        record('init')
        if init_error:
            raise RuntimeError('fake-init-secret')
        return init_result
    def profile():
        record('profile')
        if profile_error:
            raise RuntimeError('fake-profile-secret')
        return {'nickname': 'fresh'}
    def export():
        record('export')
        return {'cookie': 'fake-refreshed'}
    def signin():
        record('signin')
        return {'supported': True, 'ok': True}
    return SimpleNamespace(init=init, get_account_config=profile, export_runtime_config=export, sign_in=signin)


def test_profile_failure_keeps_authenticated_account_active(db, monkeypatch):
    session, identity = db
    events = []
    monkeypatch.setattr(service.AdapterFactory, 'create_adapter', lambda *a, **kw: adapter_for(session, events, profile_error=True))
    row = service.probe_drive_account(session, identity)
    session.commit()
    assert row.runtime_status == 'active'
    assert row.enabled and row.probe_fail_count == 0
    assert json.loads(row.profile_json)['nickname'] == 'known'
    assert row.last_error and 'fake-profile-secret' not in row.last_error
    assert row.cookie == 'fake-refreshed'


@pytest.mark.parametrize('operation', ['probe', 'signin'])
def test_adapter_factory_and_all_io_execute_without_transaction(db, monkeypatch, operation):
    session, identity = db
    events = []
    def factory(*args, **kwargs):
        events.append(('factory', session.in_transaction()))
        return adapter_for(session, events)
    monkeypatch.setattr(service.AdapterFactory, 'create_adapter', factory)
    if operation == 'probe':
        service.probe_drive_account(session, identity)
    else:
        service.sign_in_drive_account(session, identity)
    assert events and all(not in_tx for _, in_tx in events), events
    session.rollback()
    assert session.get(DriveAccount, identity).cookie == 'fake-old'


@pytest.mark.parametrize('init_error', [False, True])
def test_real_initialization_failure_increments_count_and_disables(db, monkeypatch, init_error):
    session, identity = db
    monkeypatch.setattr(service.AdapterFactory, 'create_adapter', lambda *a, **kw: adapter_for(
        session, [], init_result=False, init_error=init_error))
    row = service.probe_drive_account(session, identity)
    assert row.probe_fail_count == 3 and not row.enabled
    assert row.runtime_status == ('error' if init_error else 'inactive')
    assert 'fake-init-secret' not in (row.last_error or '')


@pytest.mark.parametrize('flushed', [False, True])
def test_uncommitted_changes_are_never_committed_by_probe(db, monkeypatch, flushed):
    session, identity = db
    session.get(DriveAccount, identity).name = 'pending'
    if flushed:
        session.flush()
    def forbidden(*args, **kwargs):
        pytest.fail('IO must not start with pending business writes')
    monkeypatch.setattr(service.AdapterFactory, 'create_adapter', forbidden)
    with pytest.raises(RuntimeError, match='read-only'):
        service.probe_drive_account(session, identity)
    session.rollback()
    assert session.get(DriveAccount, identity).name == 'test'


def test_auth_candidate_config_is_not_saved_before_network_or_on_rollback(db, monkeypatch):
    session, identity = db
    events = []
    def factory(*args, **kwargs):
        assert kwargs['config']['cookie'] == 'fake-candidate'
        with Session(session.bind) as other:
            assert other.get(DriveAccount, identity).cookie == 'fake-old'
        return adapter_for(session, events)
    monkeypatch.setattr(service.AdapterFactory, 'create_adapter', factory)
    service.probe_drive_account(session, identity, config={'cookie': 'fake-candidate'})
    assert all(not tx for _, tx in events)
    session.rollback()
    assert session.get(DriveAccount, identity).cookie == 'fake-old'


@pytest.mark.parametrize('operation', ['probe', 'signin'])
def test_concurrent_credentials_change_during_io_rejects_old_result(db, monkeypatch, operation):
    session, identity = db
    adapter = adapter_for(session, [])
    original = adapter.export_runtime_config
    def changed_during_io():
        with Session(session.bind) as other:
            row = other.get(DriveAccount, identity)
            row.cookie = 'fake-newer'
            other.commit()
        return original()
    adapter.export_runtime_config = changed_during_io
    monkeypatch.setattr(service.AdapterFactory, 'create_adapter', lambda *a, **kw: adapter)
    with pytest.raises(StaleDataError):
        if operation == 'probe':
            service.probe_drive_account(session, identity)
        else:
            service.sign_in_drive_account(session, identity)
        session.commit()
    session.rollback()
    assert session.get(DriveAccount, identity).cookie == 'fake-newer'


def test_batch_refresh_finishes_all_io_before_any_write(db, monkeypatch):
    session, identity = db
    service.create_drive_account(session, name='second', drive_type='quark', config={'cookie': 'fake-second'})
    session.commit()
    events = []
    monkeypatch.setattr(service.AdapterFactory, 'create_adapter', lambda *a, **kw: adapter_for(session, events))
    rows = service.refresh_drive_account_profiles(session)
    assert len(rows) == 2 and all(row.runtime_status == 'active' for row in rows)
    assert all(not tx for _, tx in events), events
    session.rollback()
    assert session.get(DriveAccount, identity).cookie == 'fake-old'


def test_auth_preparation_allows_cleared_credentials_but_rejects_old_session(db):
    session, _ = db
    row = service.create_drive_account(session, name='recover', drive_type='aliyun', config={'refresh_token': 'fake-old'})
    session.commit()
    identity = row.id
    service.update_drive_account(session, identity, clear_credentials=['refresh_token'])
    session.commit()
    version = service.prepare_drive_account_auth(session, identity)
    assert not session.in_transaction()
    with Session(session.bind) as other:
        other.get(DriveAccount, identity).name = 'new-name'
        other.commit()
    with pytest.raises(StaleDataError):
        service.prepare_drive_account_auth(session, identity, version)


@pytest.mark.parametrize('method,drive', [('captcha', 'quark'), ('sms', 'quark'), ('sms', 'guangya'), ('qrcode', 'aliyun')])
@pytest.mark.parametrize('race', [False, True])
def test_real_auth_routes_finish_io_before_persisting_credentials(db, monkeypatch, method, drive, race):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes import drive_accounts as route
    from app.api.deps import CurrentUser, get_current_user
    from app.core.permissions import DRIVE_ACCOUNT_WRITE
    from app.core.errors import ApiError
    from app.core.exception_handlers import api_error_handler, concurrent_update_error_handler
    from app.application.drive_account_auth_sessions import create_auth_session, delete_auth_session
    session, _ = db
    row = service.create_drive_account(session, name='auth-route', drive_type=drive,
        config={'cookie': 'fake-old', 'refresh_token': 'fake-old-refresh', 'phone_number': 'fake-phone'})
    session.commit()
    identity = row.id
    events = []
    def concurrent_change():
        if race:
            with Session(session.bind) as newer:
                newer.get(DriveAccount, identity).cookie = 'fake-concurrent'
                newer.commit()
    def submit(code):
        events.append(('submit', session.in_transaction()))
        concurrent_change()
    auth_adapter = SimpleNamespace(submit_captcha=submit, submit_sms=submit,
        export_runtime_config=lambda: {'cookie': 'fake-candidate', 'refresh_token': 'fake-candidate-refresh'})
    auth = create_auth_session(account_id=identity, drive_type=drive, method=method,
                               adapter=auth_adapter if method != 'qrcode' else {}, payload={})
    def query(*args):
        events.append(('poll', session.in_transaction()))
        concurrent_change()
        return {'success': True, 'data': {'status': 'CONFIRMED', 'refresh_token': 'fake-candidate-refresh'}}
    monkeypatch.setattr(route.AliyunAdapter, 'query_qrcode_status', query)
    def factory(*args, **kwargs):
        events.append(('factory', session.in_transaction()))
        if drive == 'aliyun':
            assert kwargs['config']['refresh_token'] == 'fake-candidate-refresh'
        adapter = adapter_for(session, events)
        if drive == 'guangya':
            def needs_sms_again():
                raise service.DriveAuthRequired(method='sms', message='profile unavailable')
            adapter.init = needs_sms_again
        return adapter
    monkeypatch.setattr(service.AdapterFactory, 'create_adapter', factory)
    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(StaleDataError, concurrent_update_error_handler)
    app.include_router(route.router, prefix='/accounts')
    app.dependency_overrides[route.get_db] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(SimpleNamespace(id=1), [], {DRIVE_ACCOUNT_WRITE})
    suffix = 'qrcode/poll' if method == 'qrcode' else ('sms/submit' if method == 'sms' else 'captcha')
    try:
        with TestClient(app) as client:
            response = client.post(f'/accounts/auth/{auth.session_id}/{suffix}', json={'code': '123456'})
        assert response.status_code == (409 if race else 200), response.text
        if race:
            session.rollback()
            assert session.get(DriveAccount, identity).cookie == 'fake-concurrent'
        else:
            assert response.json()['runtime_status'] == 'active'
        assert events and all(not tx for _, tx in events), events
    finally:
        delete_auth_session(auth.session_id)
