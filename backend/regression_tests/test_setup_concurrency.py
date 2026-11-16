from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, local

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.api.routes import setup as route
from app.application import setup
from app.application.container import get_db
from app.core.errors import ApiError
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models import AuditLog, RefreshToken, SystemSetting, User, user_roles
from app.infrastructure.persistence.repositories import system_settings


@pytest.fixture
def setup_app(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'setup.db').as_posix()}", connect_args={"check_same_thread": False, "timeout": 15})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    app = FastAPI()
    app.include_router(route.router)

    def db_dependency():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = db_dependency

    @app.exception_handler(ApiError)
    async def api_error(request, exc):
        return JSONResponse({"code": exc.code}, status_code=exc.http_status)

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, sessions
    engine.dispose()


def payload(name):
    return {"username": name, "email": f"{name}@example.com", "password": "fake-pass!123"}


def test_two_independent_requests_past_initial_check_create_one_admin(setup_app, monkeypatch):
    client, sessions = setup_app
    barrier, seen = Barrier(2), local()
    original = setup.is_initialized

    def checked(db):
        initialized = original(db)
        if not getattr(seen, "checked", False):
            seen.checked = True
            assert initialized is False
            barrier.wait(timeout=10)
        return initialized

    monkeypatch.setattr(setup, "is_initialized", checked)
    monkeypatch.setattr(route, "is_initialized", checked)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda name: client.post('/admin', json=payload(name)), ['first', 'second']))
    assert sorted(r.status_code for r in results) == [200, 409]
    assert next(r for r in results if r.status_code == 409).json()['code'] == 'SETUP_ALREADY_INITIALIZED'
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1
        assert db.scalar(select(func.count()).select_from(user_roles)) == 1
        assert db.scalar(select(func.count()).select_from(RefreshToken)) == 1
        assert db.scalar(select(func.count()).select_from(AuditLog)) == 1
        assert system_settings.get_value(db, 'system.initial_admin')


@pytest.mark.parametrize('stage', ['user', 'roles', 'token', 'audit'])
def test_failure_rolls_back_every_record_and_allows_retry(setup_app, stage):
    client, sessions = setup_app
    table = {'user': 'users', 'roles': 'user_roles', 'token': 'refresh_tokens', 'audit': 'audit_logs'}[stage]

    def fail_after_write(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith(f'INSERT INTO {table} '):
            raise RuntimeError(f'injected failure after {stage} write')

    engine = sessions.kw['bind']
    event.listen(engine, 'after_cursor_execute', fail_after_write)
    try:
        assert client.post('/admin', json=payload('retry')).status_code == 500
    finally:
        event.remove(engine, 'after_cursor_execute', fail_after_write)
    with sessions() as db:
        for model in (User, RefreshToken, AuditLog, SystemSetting):
            assert db.scalar(select(func.count()).select_from(model)) == 0
        assert db.scalar(select(func.count()).select_from(user_roles)) == 0
    assert client.post('/admin', json=payload('retry')).status_code == 200


def test_historical_install_without_claim_is_rejected(setup_app):
    client, sessions = setup_app
    with sessions.begin() as db:
        db.add(User(username='old', email='old@example.com', hashed_password='fake', is_active=True))
    response = client.post('/admin', json=payload('new'))
    assert response.status_code == 409
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 1


def test_generic_settings_cannot_overwrite_initialization_claim(setup_app):
    client, sessions = setup_app
    assert client.post('/admin', json=payload('first')).status_code == 200
    with sessions() as db:
        with pytest.raises(ValueError):
            system_settings.set_value(db, 'system.initial_admin', '')
        db.rollback()
        assert system_settings.get_value(db, 'system.initial_admin')


def test_existing_claim_rejects_setup_even_without_users(setup_app):
    client, sessions = setup_app
    with sessions.begin() as db:
        db.add(SystemSetting(key='system.initial_admin', value='1'))
    response = client.post('/admin', json=payload('new'))
    assert response.status_code == 409
    assert response.json()['code'] == 'SETUP_ALREADY_INITIALIZED'
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(User)) == 0


def test_database_unavailable_returns_503(setup_app):
    client, sessions = setup_app
    Base.metadata.drop_all(sessions.kw['bind'])
    response = client.post('/admin', json=payload('new'))
    assert response.status_code == 503
    assert response.json()['code'] == 'DB_NOT_READY'


def test_unrelated_integrity_failure_is_not_initialization_conflict(setup_app, monkeypatch):
    client, sessions = setup_app
    from sqlalchemy.exc import IntegrityError
    from app.infrastructure.persistence.repositories import users

    def unrelated(*args, **kwargs):
        raise IntegrityError('fake insert', {}, Exception('other unique field'))

    with monkeypatch.context() as patch:
        patch.setattr(users, 'create_user', unrelated)
        assert client.post('/admin', json=payload('new')).status_code == 500
    with sessions() as db:
        assert system_settings.get_value(db, 'system.initial_admin') == ''
    assert client.post('/admin', json=payload('new')).status_code == 200
