from __future__ import annotations

import json
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.services.tmdb_cache as tmdb_cache
from app.db.base import Base
from app.models.tmdb_media_cache import TMDBMediaCache
from app.models.tmdb_setting import TMDBSetting
from app.services.tmdb_cache import delete_cache_item, get_tmdb_credits_cached


class FakeTMDBClient:
    calls = 0
    names = ["张三", "李四", "王五"]
    fail = False

    def __init__(self, api_key: str, language: str = "zh-CN") -> None:
        self.api_key = api_key
        self.language = language

    def _cast(self) -> dict:
        FakeTMDBClient.calls += 1
        if FakeTMDBClient.fail:
            raise RuntimeError("tmdb boom")
        return {"cast": [{"name": n, "character": f"角色{n}"} for n in FakeTMDBClient.names]}

    def get_tv_credits(self, tv_id: int) -> dict:
        return self._cast()

    def get_movie_credits(self, movie_id: int) -> dict:
        return self._cast()


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    with Session() as session:
        session.add(TMDBSetting(id=1, config_json=json.dumps({"api_key": "test-key"})))
        session.commit()
        yield session


def _setup(monkeypatch) -> None:
    FakeTMDBClient.calls = 0
    FakeTMDBClient.fail = False
    monkeypatch.setattr(tmdb_cache, "TMDBClient", FakeTMDBClient)


def _cache_row(db):
    return db.execute(select(TMDBMediaCache)).scalars().first()


def test_credits_uses_cache_after_first_fetch(monkeypatch, db):
    _setup(monkeypatch)

    ok, cast = get_tmdb_credits_cached(db, media_type="tv", tmdb_id=1402, limit=2)
    db.commit()
    assert ok is True
    assert [c["name"] for c in cast] == ["张三", "李四"]

    # 演员表缓存在 TMDB 详情缓存的同一行上
    row = _cache_row(db)
    assert row is not None
    assert row.media_type == "tv"
    assert row.tmdb_id == 1402
    assert row.credits_json is not None

    ok2, cast2 = get_tmdb_credits_cached(db, media_type="tv", tmdb_id=1402, limit=2)
    db.commit()
    assert ok2 is True
    # 第二次命中缓存：不再请求 TMDB
    assert FakeTMDBClient.calls == 1
    assert [c["name"] for c in cast2] == ["张三", "李四"]

    # 缓存存的是前 30 位，limit 变化也走同一行
    ok3, cast3 = get_tmdb_credits_cached(db, media_type="tv", tmdb_id=1402, limit=3)
    db.commit()
    assert FakeTMDBClient.calls == 1
    assert [c["name"] for c in cast3] == ["张三", "李四", "王五"]


def test_credits_refetch_after_expiry(monkeypatch, db):
    _setup(monkeypatch)
    get_tmdb_credits_cached(db, media_type="tv", tmdb_id=1402, limit=2)
    db.commit()

    row = _cache_row(db)
    assert row is not None
    row.credits_expires_at = tmdb_cache._now() - timedelta(minutes=1)
    db.commit()

    get_tmdb_credits_cached(db, media_type="tv", tmdb_id=1402, limit=2)
    db.commit()
    assert FakeTMDBClient.calls == 2


def test_credits_network_failure_keeps_stale_cast(monkeypatch, db):
    _setup(monkeypatch)
    get_tmdb_credits_cached(db, media_type="tv", tmdb_id=1402, limit=2)
    db.commit()

    row = _cache_row(db)
    assert row is not None
    row.credits_expires_at = tmdb_cache._now() - timedelta(minutes=1)
    db.commit()

    FakeTMDBClient.fail = True
    ok, cast = get_tmdb_credits_cached(db, media_type="tv", tmdb_id=1402, limit=2)
    db.commit()
    # 网络挂了也要用旧数据顶上，文案不至于没主演
    assert ok is True
    assert [c["name"] for c in cast] == ["张三", "李四"]
    assert FakeTMDBClient.calls == 2
    assert int(row.credits_fail_count or 0) == 1


def test_credits_cleared_when_cache_item_deleted(monkeypatch, db):
    _setup(monkeypatch)
    get_tmdb_credits_cached(db, media_type="tv", tmdb_id=1402, limit=2)
    db.commit()
    assert _cache_row(db) is not None

    ok, deleted = delete_cache_item(db, media_type="tv", tmdb_id=1402)
    db.commit()
    assert ok is True
    assert deleted == 1
    assert _cache_row(db) is None


def test_credits_returns_empty_when_not_configured(monkeypatch, db):
    _setup(monkeypatch)
    setting = db.get(TMDBSetting, 1)
    setting.config_json = json.dumps({})
    db.commit()
    ok, cast = get_tmdb_credits_cached(db, media_type="tv", tmdb_id=1402, limit=2)
    assert ok is False
    assert cast == []
    assert FakeTMDBClient.calls == 0
