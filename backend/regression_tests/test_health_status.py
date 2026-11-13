import logging
import socket

import pytest
from fastapi import FastAPI
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
