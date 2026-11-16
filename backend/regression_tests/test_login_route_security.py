from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.api.routes import auth
from app.application import auth as auth_service
from app.application.login_throttle import LoginThrottle
from app.core.errors import ApiError
from app.core.exception_handlers import api_error_handler
from app.core.security import hash_password
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.user import User
from app.infrastructure.persistence.models.audit_log import AuditLog
import app.infrastructure.persistence.models  # register all tables


@pytest.fixture
def login_client(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + (tmp_path / 'auth.db').as_posix())
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(User(username='admin', email='test@example.invalid', hashed_password=hash_password('Correct!123')))
        db.commit()
    guard = LoginThrottle()
    monkeypatch.setattr(auth_service, 'login_throttle', guard)
    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(auth.router, prefix='/auth')
    def database():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[auth.get_db] = database
    with TestClient(app) as client:
        yield client, engine
    engine.dispose()


def test_route_bad_credentials_throttle_and_safe_audit(login_client):
    client, engine = login_client
    for _ in range(5):
        response = client.post('/auth/login', data={'username': 'admin', 'password': 'SecretWrong!'})
        assert response.status_code == 401
    response = client.post('/auth/login', data={'username': 'admin', 'password': 'Correct!123'})
    assert response.status_code == 429
    assert response.json()['code'] == 'AUTH_LOGIN_THROTTLED'
    assert int(response.headers['retry-after']) > 0
    with Session(engine) as db:
        rows = db.scalars(select(AuditLog)).all()
        assert len(rows) == 5
        assert all(not row.success and row.actor_user_id is None for row in rows)
        assert all('SecretWrong!' not in (row.detail or '') for row in rows)
    assert 'SecretWrong!' not in response.text


def test_success_resets_pair_and_headers_do_not_override_peer(login_client):
    client, _ = login_client
    for _ in range(4):
        assert client.post('/auth/login', data={'username': 'admin', 'password': 'wrong'}).status_code == 401
    assert client.post('/auth/login', data={'username': 'admin', 'password': 'Correct!123'}).status_code == 200
    for i in range(5):
        assert client.post('/auth/login', data={'username': 'admin', 'password': 'wrong'}, headers={'X-Forwarded-For': f'fake-{i}'}).status_code == 401
    assert client.post('/auth/login', data={'username': 'admin', 'password': 'wrong'}).status_code == 429


def test_unknown_and_inactive_users_share_401(login_client):
    client, engine = login_client
    with Session(engine) as db:
        db.scalar(select(User)).is_active = False
        db.commit()
    for name in ['admin', 'missing']:
        response = client.post('/auth/login', data={'username': name, 'password': 'wrong'})
        assert response.status_code == 401
        assert response.json()['code'] == 'AUTH_BAD_CREDENTIALS'


def test_concurrent_real_route_never_authenticates_beyond_threshold(login_client):
    client, _ = login_client
    with ThreadPoolExecutor(max_workers=10) as pool:
        responses = list(pool.map(lambda _: client.post('/auth/login', data={'username': 'missing', 'password': 'wrong'}), range(20)))
    statuses = [response.status_code for response in responses]
    assert statuses.count(401) <= 5
    assert set(statuses) == {401, 429}


def test_ip_total_limit_applies_across_usernames(login_client, monkeypatch):
    client, _ = login_client
    monkeypatch.setattr(auth_service, 'login_throttle', LoginThrottle(ip_limit=3))
    for name in ['one', 'two', 'three']:
        assert client.post('/auth/login', data={'username': name, 'password': 'wrong'}).status_code == 401
    assert client.post('/auth/login', data={'username': 'four', 'password': 'wrong'}).status_code == 429


def test_audit_failure_cannot_reset_throttle_or_fake_success(login_client, monkeypatch, caplog):
    client, _ = login_client
    def broken(*args, **kwargs):
        raise RuntimeError('fake-password-must-not-escape')
    monkeypatch.setattr(auth_service.audit, 'write_audit_log', broken)
    for _ in range(5):
        assert client.post('/auth/login', data={'username': 'missing', 'password': 'wrong'}).status_code == 401
    assert client.post('/auth/login', data={'username': 'missing', 'password': 'wrong'}).status_code == 429
    assert 'audit could not be persisted' in caplog.text
    assert 'fake-password' not in caplog.text


def test_database_error_releases_admission(login_client, monkeypatch):
    client, _ = login_client
    repo = auth_service.users_repo()
    original = repo.get_user_by_username
    def broken(*args):
        raise RuntimeError('database unavailable')
    with monkeypatch.context() as patch:
        patch.setattr(repo, 'get_user_by_username', broken)
        with pytest.raises(RuntimeError):
            client.post('/auth/login', data={'username': 'admin', 'password': 'wrong'})
    assert repo.get_user_by_username == original
    assert client.post('/auth/login', data={'username': 'admin', 'password': 'Correct!123'}).status_code == 200


def test_proxy_middleware_only_trusts_configured_peer(login_client):
    client, engine = login_client
    # Uvicorn's real middleware supplies the trusted peer IP to the real route.
    wrapped = ProxyHeadersMiddleware(client.app, trusted_hosts=['127.0.0.1'])
    with TestClient(wrapped, client=('127.0.0.1', 1234)) as proxy_client:
        proxy_client.post('/auth/login', data={'username': 'absent', 'password': 'wrong'},
                          headers={'X-Forwarded-For': '203.0.113.8'})
    with TestClient(wrapped, client=('192.0.2.1', 1234)) as direct_client:
        direct_client.post('/auth/login', data={'username': 'absent', 'password': 'wrong'},
                           headers={'X-Forwarded-For': 'spoofed'})
    with Session(engine) as db:
        assert set(db.scalars(select(AuditLog.ip))) == {'203.0.113.8', '192.0.2.1'}


def test_login_refresh_logout_remain_usable(login_client):
    client, _ = login_client
    result = client.post('/auth/login', data={'username': 'admin', 'password': 'Correct!123'})
    assert result.status_code == 200
    assert result.json()['user']['username'] == 'admin'
    assert client.post('/auth/refresh').status_code == 200
    assert client.post('/auth/logout', headers={'Authorization': 'Bearer ' + result.json()['access_token']}).status_code == 200
    assert client.post('/auth/refresh').status_code == 401


def test_failed_success_commit_does_not_reset_failures(login_client, monkeypatch):
    client, _ = login_client
    for _ in range(4):
        assert client.post('/auth/login', data={'username': 'admin', 'password': 'wrong'}).status_code == 401
    def broken(db):
        raise RuntimeError('commit failed')
    with monkeypatch.context() as patch:
        patch.setattr(auth, 'commit', broken)
        with pytest.raises(RuntimeError):
            client.post('/auth/login', data={'username': 'admin', 'password': 'Correct!123'})
    assert client.post('/auth/login', data={'username': 'admin', 'password': 'wrong'}).status_code == 401
    assert client.post('/auth/login', data={'username': 'admin', 'password': 'wrong'}).status_code == 429


@pytest.mark.parametrize('field', ['pair_limit', 'ip_limit', 'window_seconds', 'cooldown_seconds', 'capacity'])
def test_login_configuration_requires_positive_limits(field):
    from app.core.settings import Settings
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        Settings(**{'login_throttle_' + field: 0})
