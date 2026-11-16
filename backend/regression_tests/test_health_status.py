import logging
import socket

import pytest
from fastapi import FastAPI, Response
from types import SimpleNamespace
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes import health


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("BUILD_TAG", "test-build")
    monkeypatch.setenv("BUILD_SHA", "abc123")
    app = FastAPI()
    app.include_router(health.router, prefix="/api")
    return app


def test_readiness_preserves_fields_with_healthy_database(app):
    engine = create_engine("sqlite://")

    def database():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[health.get_db] = database
    with TestClient(app) as client:
        response = client.get("/api/health")
    engine.dispose()
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok", "db_connected": True, "time": response.json()["time"],
        "build_tag": "test-build", "build_sha": "abc123",
    }


def test_readiness_database_failure_is_503_safe_and_releases_session(app, caplog):
    released = []

    class BrokenSession:
        def execute(self, statement):
            raise RuntimeError("postgres://user:secret@host/db token=private")

    def database():
        try:
            yield BrokenSession()
        finally:
            released.append(True)

    app.dependency_overrides[health.get_db] = database
    with caplog.at_level(logging.WARNING), TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["db_connected"] is False
    assert released == [True]
    assert "secret" not in response.text + caplog.text
    assert "private" not in response.text + caplog.text
    assert any("health" in record.message.lower() for record in caplog.records)


def test_liveness_does_not_resolve_database_or_open_network(app, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Liveness must not access dependencies or network")

    app.dependency_overrides[health.get_db] = forbidden
    with TestClient(app) as client:
        with monkeypatch.context() as guard:
            guard.setattr(socket.socket, "connect", forbidden)
            response = client.get("/api/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_build_environment_does_not_start_git(app, monkeypatch):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("build environment must take precedence")
    monkeypatch.setattr(health.subprocess, "run", forbidden)
    response = health.health(Response(), db=WorkingDatabase())
    assert response["build_tag"] == "test-build"
    assert response["build_sha"] == "abc123"
    assert calls == []


class WorkingDatabase:
    def __init__(self):
        self.probes = 0

    def execute(self, statement):
        self.probes += 1


@pytest.mark.parametrize("git_available", [True, False])
def test_git_fallback_is_cached_but_database_is_probed_each_time(monkeypatch, git_available):
    monkeypatch.delenv("BUILD_TAG", raising=False)
    monkeypatch.delenv("BUILD_SHA", raising=False)
    calls = []

    def git(*args, **kwargs):
        calls.append(args)
        assert kwargs["timeout"] == 5
        if not git_available:
            raise FileNotFoundError("no git")
        return SimpleNamespace(stdout="test-git\n")

    monkeypatch.setattr(health.subprocess, "run", git)
    db = WorkingDatabase()
    try:
        first = health.health(Response(), db=db)
        second = health.health(Response(), db=db)
        assert len(calls) == 2
        assert db.probes == 2
        assert first["build_sha"] == second["build_sha"] == ("test-git" if git_available else None)
        assert first["build_tag"] == second["build_tag"] == ("test-git" if git_available else "dev")
    finally:
        for function in (health._git_describe, health._git_sha):
            if hasattr(function, "cache_clear"):
                function.cache_clear()
