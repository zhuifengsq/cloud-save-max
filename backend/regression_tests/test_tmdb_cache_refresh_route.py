"""TMDB 缓存刷新端点：点一下必须真的能跑通（2026-11-09 「服务器内部错误」事故护栏）。

事故根因是 `post_tmdb_cache_refresh` 里留下了未定义的 `lookup`（仓储下沉只换了一半），
以及 `commit` 忘了 import；这两处在真实请求里都会抛 `NameError` → HTTP 500。
本测试直接调用路由函数（不经过 HTTP/鉴权），配真实临时 SQLite 与假 TMDB 网络层，
把「点刷新」这条路径整体跑一遍。
"""
from __future__ import annotations

import json

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.api.routes import tmdb_cache as route
from app.application import tmdb_cache as service
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.audit_log import AuditLog
from app.infrastructure.persistence.models.tmdb_media_cache import TMDBMediaCache
from app.infrastructure.persistence.models.tmdb_setting import TMDBSetting
from app.schemas.tmdb_cache import TMDBCacheRefreshIn


class _FakeClient:
    host = "127.0.0.1"


class _FakeRequest:
    client = _FakeClient()
    headers = {"user-agent": "pytest"}


class _FakeUser:
    id = 1


class _FakeCurrentUser:
    user = _FakeUser()


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'route.db').as_posix()}")
    Base.metadata.create_all(
        engine,
        tables=[TMDBSetting.__table__, TMDBMediaCache.__table__, AuditLog.__table__],
    )
    db = sessionmaker(bind=engine)()
    db.add(
        TMDBSetting(
            id=1,
            config_json=json.dumps(
                {"api_key": "test-key", "language": "zh-CN", "poster_language": "zh-CN"}
            ),
        )
    )
    db.commit()
    return db


def _patch_network(monkeypatch, calls):
    def fake_fetch(client, *, media_type, tmdb_id, language, poster_language):
        calls.append((media_type, tmdb_id))
        return {"id": tmdb_id, "title": "probe"}, [], []

    monkeypatch.setattr(service, "_fetch_tmdb_detail", fake_fetch)


def test_refresh_endpoint_returns_status_instead_of_500(tmp_path, monkeypatch):
    db = _session(tmp_path)
    calls: list[tuple[str, int]] = []
    try:
        _patch_network(monkeypatch, calls)
        out = route.post_tmdb_cache_refresh(
            _FakeRequest(),
            TMDBCacheRefreshIn(media_type="movie", tmdb_id=42, force=True, async_refresh=False),
            _FakeCurrentUser(),
            db,
        )
        # 刷新前库里没有该条 → exists=False；但状态必须是「已配置 + 没报错」，而不是 500
        assert out.queued is False
        assert out.status.configured is True
        assert out.status.media_type == "movie"
        assert out.status.tmdb_id == 42
        assert calls == [("movie", 42)], "同步刷新应真的走到网络层"
    finally:
        db.close()


def test_refresh_endpoint_reports_fresh_row_and_writes_audit(tmp_path, monkeypatch):
    db = _session(tmp_path)
    calls: list[tuple[str, int]] = []
    try:
        _patch_network(monkeypatch, calls)
        payload = TMDBCacheRefreshIn(media_type="tv", tmdb_id=7, force=True, async_refresh=False)
        route.post_tmdb_cache_refresh(_FakeRequest(), payload, _FakeCurrentUser(), db)
        # 第二次调用：库里已经有缓存行 → 提交后必须能读到最新状态（原来这行是 db.scalar(lookup) 的 NameError）
        out = route.post_tmdb_cache_refresh(_FakeRequest(), payload, _FakeCurrentUser(), db)
        assert out.status.exists is True
        assert out.status.tmdb_id == 7
        audit_actions = {row[0] for row in db.execute(select(AuditLog.action)).all()}
        assert "tmdb.cache.refresh" in audit_actions
        audit_count = db.execute(select(func.count()).select_from(AuditLog)).scalar_one()
        assert audit_count == 2, "每次刷新都应留下一条审计"
    finally:
        db.close()
