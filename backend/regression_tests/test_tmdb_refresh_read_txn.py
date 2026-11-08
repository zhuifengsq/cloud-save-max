"""TMDB 刷新：长批量刷新不得在外部 HTTP 期间持有调用方读事务（WAL checkpoint 护栏）。

背景：刷新链路的领标记/写回已走独立 writer 引擎，但调用方 Session 在读完配置后会把
读事务一直开到整批结束（最多 200 次网络请求）。SQLite WAL 的 checkpoint 要求所有读
事务结束，否则 -wal 持续增长（方案 §7 R14）。close_read_tx=True 时服务会在外部请求
前结束调用方事务；默认 False 保持旧语义（任务执行器等调用方可能持有未提交写入）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.tmdb_media_cache import TMDBMediaCache
from app.infrastructure.persistence.models.tmdb_setting import TMDBSetting
from app.application import tmdb_cache


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'tmdb.db').as_posix()}")
    Base.metadata.create_all(
        engine,
        tables=[TMDBSetting.__table__, TMDBMediaCache.__table__],
    )
    factory = sessionmaker(bind=engine)
    db = factory()
    db.add(TMDBSetting(id=1, config_json=json.dumps({"api_key": "test-key"})))
    db.commit()
    return db


def _patch_fetch(monkeypatch, db, observed):
    def fake_fetch(client, *, media_type, tmdb_id, language, poster_language):
        observed.append(bool(db.in_transaction()))
        return {"id": tmdb_id, "title": "probe"}, [], []

    monkeypatch.setattr(tmdb_cache, "_fetch_tmdb_detail", fake_fetch)


def test_close_read_tx_closes_caller_session_before_http(tmp_path, monkeypatch):
    db = _session(tmp_path)
    observed: list[bool] = []
    try:
        _patch_fetch(monkeypatch, db, observed)
        tmdb_cache.refresh_tmdb_detail_sync(
            db,
            media_type="movie",
            tmdb_id=1,
            language="zh-CN",
            poster_language="zh-CN",
            force=True,
            close_read_tx=True,
        )
    finally:
        db.close()
    assert observed == [False], "外部请求期间调用方不应持有读事务"


def test_default_keeps_previous_semantics(tmp_path, monkeypatch):
    db = _session(tmp_path)
    observed: list[bool] = []
    try:
        _patch_fetch(monkeypatch, db, observed)
        tmdb_cache.refresh_tmdb_detail_sync(
            db,
            media_type="movie",
            tmdb_id=2,
            language="zh-CN",
            poster_language="zh-CN",
            force=True,
        )
    finally:
        db.close()
    assert observed == [True], "默认路径必须保持旧行为（执行器可能持有未提交写入）"


def test_batch_refresh_closes_read_tx_between_items(tmp_path, monkeypatch):
    db = _session(tmp_path)
    observed: list[bool] = []
    try:
        db.add(
            TMDBMediaCache(
                media_type="movie",
                tmdb_id=3,
                language="zh-CN",
                poster_language="zh-CN",
                expires_at=datetime.now() - timedelta(hours=1),
            )
        )
        db.commit()
        _patch_fetch(monkeypatch, db, observed)
        result = tmdb_cache.refresh_expired_cache(db, max_items=1, force=True, close_read_tx=True)
    finally:
        db.close()
    assert result["targets"] == 1
    assert observed == [False], "整批刷新不应共用一个读快照"
