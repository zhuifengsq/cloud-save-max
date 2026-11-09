"""TMDB 刷新：SQLite 写锁争用时不能再「静默跳过」（假成功）护栏。

背景：旧实现里 `_cache_writer` 的连接只等 0.5s 写锁，领刷新标记失败会被当成
「别人正在刷」而静默返回旧缓存 —— HTTP 200 + 旧数据，前端弹「已刷新」但页面没变。
现在：写锁等待放宽 + 有限重试；用户主动刷新（raise_on_busy=True）仍失败则抛
TMDBCacheBusy，路由映射成 503；后台/执行器路径保持静默旧语义。
"""
from __future__ import annotations

import json
import sqlite3
import types
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes.tmdb_cache import post_tmdb_cache_refresh
from app.core.errors import ApiError
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.tmdb_media_cache import TMDBMediaCache
from app.infrastructure.persistence.models.tmdb_setting import TMDBSetting
from app.application import tmdb_cache
from app.schemas.tmdb_cache import TMDBCacheRefreshIn


def _setup(tmp_path):
    db_path = tmp_path / "tmdb_busy.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    Base.metadata.create_all(engine, tables=[TMDBSetting.__table__, TMDBMediaCache.__table__])
    factory = sessionmaker(bind=engine)
    db = factory()
    db.add(TMDBSetting(id=1, config_json=json.dumps({"api_key": "test-key"})))
    db.add(
        TMDBMediaCache(
            media_type="tv",
            tmdb_id=279923,
            language="zh-CN",
            poster_language="zh-CN",
            payload_json=json.dumps({"id": 279923, "name": "旧标题"}),
            display_title="旧标题",
            fetched_at=datetime.now() - timedelta(days=1),
            expires_at=datetime.now() - timedelta(hours=1),
            fail_count=0,
        )
    )
    db.commit()
    return db, db_path


def _hold_write_lock(db_path):
    conn = sqlite3.connect(str(db_path), timeout=0.1, isolation_level=None)
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE tmdb_media_cache SET fail_count = fail_count")
    return conn


def _fast_waits(monkeypatch):
    monkeypatch.setattr(tmdb_cache, "_WRITER_CONNECT_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(tmdb_cache, "_WRITE_SLOTS_TIMEOUT_SECONDS", 0.5)
    monkeypatch.setattr(tmdb_cache, "_CLAIM_RETRY_DELAY_SECONDS", 0.01)


def _patch_fetch(monkeypatch, calls):
    def fake_fetch(client, *, media_type, tmdb_id, language, poster_language):
        calls.append((media_type, tmdb_id))
        return {"id": tmdb_id, "name": "新标题"}, [], []

    monkeypatch.setattr(tmdb_cache, "_fetch_tmdb_detail", fake_fetch)


def _refresh(db, *, raise_on_busy):
    return tmdb_cache.refresh_tmdb_detail_sync(
        db,
        media_type="tv",
        tmdb_id=279923,
        language="zh-CN",
        poster_language="zh-CN",
        force=True,
        raise_on_busy=raise_on_busy,
    )


def _row(db):
    db.expire_all()
    return db.query(TMDBMediaCache).filter_by(media_type="tv", tmdb_id=279923, language="zh-CN", poster_language="zh-CN").one()


def test_busy_write_lock_raises_instead_of_silently_skipping(tmp_path, monkeypatch):
    db, db_path = _setup(tmp_path)
    calls: list = []
    _fast_waits(monkeypatch)
    _patch_fetch(monkeypatch, calls)
    lock = _hold_write_lock(db_path)
    before = _row(db).fetched_at
    try:
        with pytest.raises(tmdb_cache.TMDBCacheBusy):
            _refresh(db, raise_on_busy=True)
    finally:
        lock.execute("ROLLBACK")
        lock.close()
        db.close()
    assert calls == [], "写锁被占用时不应发起 TMDB 请求"
    assert before is not None


def test_user_retry_succeeds_after_lock_released(tmp_path, monkeypatch):
    db, db_path = _setup(tmp_path)
    calls: list = []
    _fast_waits(monkeypatch)
    _patch_fetch(monkeypatch, calls)
    lock = _hold_write_lock(db_path)
    try:
        with pytest.raises(tmdb_cache.TMDBCacheBusy):
            _refresh(db, raise_on_busy=True)
    finally:
        lock.execute("ROLLBACK")
        lock.close()
    try:
        row, details, _weekdays, _episodes = _refresh(db, raise_on_busy=True)
    finally:
        db.close()
    assert calls == [("tv", 279923)], "锁释放后重试必须真的去拉 TMDB"
    assert row is not None and row.refresh_in_progress is False
    assert isinstance(details, dict) and details.get("name") == "新标题"


def test_background_callers_keep_silent_legacy_behaviour(tmp_path, monkeypatch):
    db, db_path = _setup(tmp_path)
    calls: list = []
    _fast_waits(monkeypatch)
    _patch_fetch(monkeypatch, calls)
    lock = _hold_write_lock(db_path)
    try:
        row, details, _weekdays, _episodes = _refresh(db, raise_on_busy=False)
    finally:
        lock.execute("ROLLBACK")
        lock.close()
        db.close()
    assert calls == [], "后台路径不因锁争用而抛错，也不应浪费 TMDB 请求"
    assert details is None or details.get("name") == "旧标题"


def test_refresh_route_returns_503_when_cache_busy(tmp_path, monkeypatch):
    db, db_path = _setup(tmp_path)
    _fast_waits(monkeypatch)
    _patch_fetch(monkeypatch, [])
    request = types.SimpleNamespace(client=types.SimpleNamespace(host="127.0.0.1"), headers={})
    current = types.SimpleNamespace(user=types.SimpleNamespace(id=1))
    payload = TMDBCacheRefreshIn(media_type="tv", tmdb_id=279923, force=True, async_refresh=False)
    lock = _hold_write_lock(db_path)
    try:
        with pytest.raises(ApiError) as excinfo:
            post_tmdb_cache_refresh(request, payload, current, db)
    finally:
        lock.execute("ROLLBACK")
        lock.close()
        db.close()
    assert excinfo.value.code == "TMDB_CACHE_BUSY"
    assert excinfo.value.http_status == 503
