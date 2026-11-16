import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.application import drive_accounts as service
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.drive_account import DriveAccount
import app.infrastructure.persistence.models


@pytest.fixture
def database(tmp_path):
    engine = create_engine('sqlite:///' + (tmp_path / 'accounts.db').as_posix())
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


@pytest.mark.parametrize('kind', ['quark', 'uc', 'baidu', '115', 'aliyun', 'xunlei', '123pan', 'cloud139', 'cloud189', 'guangya'])
def test_serialized_accounts_never_return_secrets_or_raw_errors(database, kind):
    config = dict(service.AdapterRegistry.get_drive_type_meta(kind)['default_config'])
    config.update(cookie='fake-cookie-sensitive', password='fake-password-sensitive', refresh_token='fake-refresh-sensitive',
                  authorization='fake-auth-sensitive', access_token='fake-access-sensitive', loginUuid='fake-session-sensitive',
                  future_token='fake-unknown-sensitive')
    account = DriveAccount(name='example', drive_type=kind, cookie='fake-cookie-sensitive', config_json=json.dumps(config),
        profile_json=json.dumps({'nickname': 'example', 'used_space': 10, 'raw': {'cookie': 'fake-cookie-sensitive'}}),
        last_error='URL?token=fake-refresh-sensitive')
    database.add(account)
    database.commit()
    body = service.serialize_drive_account(account)
    assert 'sensitive' not in json.dumps(body, default=str)
    assert body['credential_present'] is True
    assert body['profile']['nickname'] == 'example'
    assert body['used_space'] == 10


def test_public_config_edit_preserves_omitted_secrets(database):
    account = service.create_drive_account(database, name='one', drive_type='123pan',
        config={'username': 'fake-user', 'password': 'fake-pass', 'authorization': 'fake-token'})
    database.commit()
    service.update_drive_account(database, account.id, name='renamed', config={'protocol': 'https'})
    stored = service.resolve_drive_account_config(account)
    assert stored['password'] == 'fake-pass'
    assert stored['authorization'] == 'fake-token'
    assert stored['protocol'] == 'https'


def test_explicit_clear_and_mask_rejection(database):
    from app.core.errors import ApiError
    account = service.create_drive_account(database, name='one', drive_type='quark', config={'cookie': 'fake-cookie'})
    database.commit()
    with pytest.raises(ApiError):
        service.update_drive_account(database, account.id, config={'cookie': '********'})
    service.update_drive_account(database, account.id, clear_credentials=['cookie'])
    assert not service.resolve_drive_account_config(account)['cookie']


def test_legacy_cookie_update_uses_same_mask_and_merge_protection(database):
    from app.core.errors import ApiError
    account = service.create_drive_account(database, name='legacy', drive_type='123pan',
        config={'username': 'fake-user', 'password': 'fake-password', 'authorization': 'fake-token'})
    database.commit()
    with pytest.raises(ApiError) as error:
        service.update_drive_account(database, account.id, cookie='********')
    assert error.value.code == 'DRIVE_CREDENTIAL_MASK_INVALID'
    with pytest.raises(ApiError):
        service.update_drive_account(database, account.id, cookie='password=********')
    service.update_drive_account(database, account.id, cookie='password=replaced')
    database.commit()
    config = service.resolve_drive_account_config(account)
    assert config['password'] == 'replaced'
    assert config['authorization'] == 'fake-token'
    assert config['username'] == 'fake-user'
    service.update_drive_account(database, account.id, cookie='')
    database.commit()
    assert service.resolve_drive_account_config(account)['password'] == 'replaced'


def test_actual_read_route_hides_credentials(database):
    from app.api.routes import drive_accounts as route
    from app.api.deps import CurrentUser, get_current_user
    from app.core.permissions import DRIVE_ACCOUNT_READ, DRIVE_ACCOUNT_WRITE
    from app.core.errors import ApiError
    from app.core.exception_handlers import api_error_handler
    service.create_drive_account(database, name='api', drive_type='quark', config={'cookie': 'fake-secret-cookie'})
    database.commit()
    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(route.router, prefix='/accounts')
    app.dependency_overrides[route.get_db] = lambda: database
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(SimpleNamespace(id=1), [], {DRIVE_ACCOUNT_READ, DRIVE_ACCOUNT_WRITE})
    with TestClient(app) as client:
        response = client.get('/accounts')
        assert response.status_code == 200
        account_id = response.json()[0]['id']
        updated = client.patch(f'/accounts/{account_id}', json={'config': {}, 'name': 'renamed'})
        assert updated.status_code == 200
        assert 'fake-secret-cookie' not in updated.text
        assert updated.json()['credential_present'] is True
        cleared = client.patch(f'/accounts/{account_id}', json={'clear_credentials': ['cookie']})
        assert cleared.status_code == 200
        assert cleared.json()['credential_present'] is False
    assert response.status_code == 200
    assert 'fake-secret-cookie' not in response.text
    assert response.json()[0]['credential_present'] is True


@pytest.mark.parametrize('kind', ['exception', 'provider_failure'])
def test_signin_failures_do_not_leak_to_response_or_audit(database, monkeypatch, kind):
    from app.api.routes import drive_accounts as route
    from app.api.deps import CurrentUser, get_current_user
    from app.core.permissions import DRIVE_ACCOUNT_WRITE
    from app.core.errors import ApiError
    from app.core.exception_handlers import api_error_handler
    from app.infrastructure.persistence.models.audit_log import AuditLog
    account = service.create_drive_account(database, name='signin', drive_type='quark', config={'cookie': 'fake-cookie'})
    database.commit()
    def signin():
        if kind == 'exception':
            raise RuntimeError('fake-signin-secret')
        return {'supported': True, 'ok': False, 'message': 'fake-signin-secret', 'reward': 'fake-reward-secret'}
    monkeypatch.setattr(service.AdapterFactory, 'create_adapter', lambda *a, **kw: SimpleNamespace(sign_in=signin))
    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(route.router, prefix='/accounts')
    app.dependency_overrides[route.get_db] = lambda: database
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(SimpleNamespace(id=1), [], {DRIVE_ACCOUNT_WRITE})
    with TestClient(app) as client:
        response = client.post(f'/accounts/{account.id}/sign-in')
    assert response.status_code in (400, 502)
    assert 'secret' not in response.text
    assert 'secret' not in ' '.join(row.detail or '' for row in database.query(AuditLog))


def test_scheduled_probe_failure_does_not_forward_exception_secrets(database, monkeypatch, caplog):
    from sqlalchemy.orm import sessionmaker
    from app.infrastructure.scheduling import task_scheduler as scheduler
    account = service.create_drive_account(database, name='scheduled', drive_type='quark', config={'cookie': 'fake-cookie'})
    database.commit()
    identity = account.id
    monkeypatch.setattr(scheduler, 'SessionLocal', sessionmaker(database.bind))
    def broken(*args):
        raise RuntimeError('fake-probe-secret')
    monkeypatch.setattr(scheduler, 'probe_drive_account', broken)
    notifications = []
    monkeypatch.setattr(scheduler, 'send_runtime', lambda db, title, content: notifications.append(content))
    scheduler.run_drive_account_probe()
    assert notifications
    assert 'fake-probe-secret' not in '\n'.join(notifications)
    assert 'fake-probe-secret' not in caplog.text
    database.expire_all()
    assert 'fake-probe-secret' not in (database.get(DriveAccount, identity).last_error or '')


def test_scheduled_probe_conflict_preserves_new_credentials_and_status(database, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from app.infrastructure.scheduling import task_scheduler as scheduler
    account = service.create_drive_account(database, name='race-probe', drive_type='quark',
        config={'cookie': 'fake-old'}, runtime_status='active')
    account.runtime_status = 'active'
    database.commit()
    identity = account.id
    sessions = sessionmaker(database.bind)
    monkeypatch.setattr(scheduler, 'SessionLocal', sessions)
    def racing_probe(db, account_id):
        stale = db.get(DriveAccount, account_id)
        stale.profile_json = json.dumps({'nickname': 'stale-probe'})
        with sessions() as newer:
            fresh = newer.get(DriveAccount, account_id)
            fresh.cookie = 'fake-new'
            fresh.runtime_status = 'active'
            fresh.last_error = None
            newer.commit()
        return stale
    monkeypatch.setattr(scheduler, 'probe_drive_account', racing_probe)
    notifications = []
    monkeypatch.setattr(scheduler, 'send_runtime', lambda db, title, content: notifications.append(content))
    scheduler.run_drive_account_probe()
    database.expire_all()
    fresh = database.get(DriveAccount, identity)
    assert fresh.cookie == 'fake-new'
    assert fresh.runtime_status == 'active'
    assert fresh.last_error is None
    assert not notifications


def test_signin_stale_flush_returns_409_and_preserves_new_credentials(database, monkeypatch):
    from app.api.routes import drive_accounts as route
    from app.api.deps import CurrentUser, get_current_user
    from app.core.permissions import DRIVE_ACCOUNT_WRITE
    from app.core.exception_handlers import concurrent_update_error_handler
    from sqlalchemy.orm.exc import StaleDataError
    account = service.create_drive_account(database, name='signin-race', drive_type='quark', config={'cookie': 'fake-old'})
    database.commit()
    identity = account.id
    assert account.cookie == 'fake-old'
    with Session(database.bind) as newer:
        fresh = newer.get(DriveAccount, identity)
        fresh.cookie = 'fake-new'
        newer.commit()
    adapter = SimpleNamespace(sign_in=lambda: {'supported': True, 'ok': True},
        export_runtime_config=lambda: {'cookie': 'fake-stale-refresh'})
    monkeypatch.setattr(service.AdapterFactory, 'create_adapter', lambda *a, **kw: adapter)
    app = FastAPI()
    app.add_exception_handler(StaleDataError, concurrent_update_error_handler)
    app.include_router(route.router, prefix='/accounts')
    app.dependency_overrides[route.get_db] = lambda: database
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(SimpleNamespace(id=1), [], {DRIVE_ACCOUNT_WRITE})
    with TestClient(app) as client:
        response = client.post(f'/accounts/{identity}/sign-in')
    assert response.status_code == 409
    assert response.json()['code'] == 'DRIVE_ACCOUNT_CONFLICT'
    assert 'fake-old' not in response.text and 'fake-new' not in response.text
    database.rollback()
    assert database.get(DriveAccount, identity).cookie == 'fake-new'

