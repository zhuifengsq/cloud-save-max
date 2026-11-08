from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
import json
import sqlite3
from threading import Event

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session

from app.infrastructure.persistence.models.tmdb_media_cache import TMDBMediaCache
from app.infrastructure.persistence.models.tmdb_setting import TMDBSetting
from app.application import tmdb_cache as cache


@pytest.fixture
def database(tmp_path, request):
    path = tmp_path / "refresh.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE unrelated (id INTEGER)")
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"timeout": 0.2},
        pool_size=getattr(request, "param", 5), max_overflow=0, pool_timeout=0.2,
    )
    try:
        TMDBMediaCache.__table__.create(engine)
        TMDBSetting.__table__.create(engine)
        with Session(engine) as db:
            db.add(TMDBSetting(id=1, config_json=json.dumps({"api_key": "mock"})))
            db.commit()
        yield engine, path
    finally:
        engine.dispose()


@pytest.mark.parametrize("database", [1], indirect=True)
def test_detail_network_wait_allows_other_writes_and_keeps_caller_transaction(database, monkeypatch):
    engine, path = database
    write_errors = []

    def fetch(*args, **kwargs):
        with closing(sqlite3.connect(path, timeout=0.05)) as conn:
            try:
                conn.execute("INSERT INTO unrelated VALUES (1)")
                conn.commit()
            except sqlite3.OperationalError as exc:
                write_errors.append(str(exc))
        return {"title": "独立回填"}, [], []

    monkeypatch.setattr(cache, "_fetch_tmdb_detail", fetch)
    with Session(engine, autoflush=False) as db:
        db.add(TMDBSetting(id=2, config_json="{}"))
        _, details, _, _, _ = cache.get_tmdb_detail_cached(db, media_type="movie", tmdb_id=123)
        assert details == {"title": "独立回填"}
        assert write_errors == []
        db.rollback()
    with Session(engine) as db:
        assert db.get(TMDBSetting, 2) is None
        assert db.scalar(select(TMDBMediaCache)).display_title == "独立回填"


def test_credits_first_fill_is_coalesced_and_delete_wins(database, monkeypatch):
    engine, _ = database
    started = Event()
    finish = Event()
    calls = []

    class Client:
        def get_movie_credits(self, tmdb_id):
            calls.append(tmdb_id)
            started.set()
            assert finish.wait(3)
            return {"cast": [{"name": "演员", "character": "角色"}]}

    monkeypatch.setattr(cache, "_new_tmdb_client", lambda **kw: Client())

    def fetch():
        with Session(engine) as db:
            return cache.get_tmdb_credits_cached(db, media_type="movie", tmdb_id=123)

    with ThreadPoolExecutor(max_workers=1) as executor:
        worker = executor.submit(fetch)
        try:
            assert started.wait(2)
            # A concurrent request sees the in-flight miss and does not call the upstream.
            assert fetch() == (True, [])
            with Session(engine) as db:
                assert cache.delete_cache_item(db, media_type="movie", tmdb_id=123) == (True, 1)
                db.commit()
        finally:
            finish.set()
        assert worker.result(timeout=2) == (True, [])
    assert calls == [123]
    with Session(engine) as db:
        assert db.scalar(select(TMDBMediaCache)) is None


def test_expired_refresh_owner_cannot_overwrite_newer_result(database, monkeypatch):
    engine, _ = database

    def refresh(language):
        with Session(engine) as db:
            return cache.refresh_tmdb_detail_sync(
                db, media_type="movie", tmdb_id=123, language=language, poster_language=language, force=True,
            )

    def delayed(*args, **kwargs):
        with Session(engine) as db, db.begin():
            db.execute(update(TMDBMediaCache).values(refresh_started_at=cache._now() - timedelta(hours=1)))
        monkeypatch.setattr(cache, "_fetch_tmdb_detail", lambda *a, **kw: ({"title": kw["poster_language"]}, [], []))
        assert refresh("en-US")[1] == {"title": "en-US"}
        assert refresh("zh-CN")[1] == {"title": "zh-CN"}
        return {"title": "expired response"}, [], []

    monkeypatch.setattr(cache, "_fetch_tmdb_detail", delayed)
    assert refresh("zh-CN")[1] == {"title": "zh-CN"}
    with Session(engine) as db:
        rows = db.scalars(select(TMDBMediaCache).order_by(TMDBMediaCache.language)).all()
        assert [(row.language, row.display_title, row.refresh_started_at) for row in rows] == [
            ("en-US", "en-US", None), ("zh-CN", "zh-CN", None),
        ]
