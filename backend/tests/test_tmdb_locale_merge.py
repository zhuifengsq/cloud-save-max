from __future__ import annotations

import json
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

import app.application.tmdb_cache as tmdb_cache
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.tmdb_media_cache import TMDBMediaCache
from app.infrastructure.persistence.models.tmdb_setting import TMDBSetting
from app.application.tmdb_cache import _fetch_tmdb_detail, _season_needs_base, get_tmdb_detail_cached, purge_cold_cache
from app.infrastructure.http.tmdb_client import BASE_LANGUAGE, TMDBClient


class FakeLocaleClient:
    """模拟 TMDB：基础记录(language=xx)是准确的事实数据，zh-CN 翻译记录里的日期/时长是旧的。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None, str | None]] = []

    def get_tv_details(self, tv_id: int, *, language: str | None = None) -> dict:
        self.calls.append(("details", tv_id, language))
        if language == BASE_LANGUAGE:
            return {
                "id": tv_id,
                "name": "Today is a Rainy Day",
                "status": "Returning Series",
                "last_air_date": "2026-11-06",
                "number_of_seasons": 1,
                "number_of_episodes": 24,
                "seasons": [{"season_number": 1, "episode_count": 24}],
                "next_episode_to_air": {"episode_number": 4, "air_date": "2026-11-07", "runtime": 52},
            }
        return {
            "id": tv_id,
            "name": "今天是太阳雨",
            "status": "Returning Series",
            "last_air_date": None,
            "number_of_seasons": 1,
            "number_of_episodes": 24,
            "seasons": [{"season_number": 1, "episode_count": 24}],
            "next_episode_to_air": {"episode_number": 4, "air_date": "2026-11-07", "runtime": None},
        }

    def get_tv_season(self, tv_id: int, season_number: int, *, language: str | None = None) -> dict:
        self.calls.append(("season", season_number, language))
        if language == BASE_LANGUAGE:
            return {
                "season_number": season_number,
                "air_date": "2026-11-06",
                "episodes": [
                    {"episode_number": n, "name": f"Episode {n}", "air_date": "2026-11-07", "runtime": 46, "overview": ""}
                    for n in (5, 6)
                ],
            }
        return {
            "season_number": season_number,
            "air_date": "2026-11-06",
            "episodes": [
                {"episode_number": n, "name": f"第 {n} 集", "air_date": "2026-11-08", "runtime": None, "overview": "本地化简介"}
                for n in (5, 6)
            ],
        }


@pytest.fixture()
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 't.db'}", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    with Session() as session:
        session.add(TMDBSetting(id=1, config_json=json.dumps({"api_key": "test-key", "language": "zh-CN"})))
        session.commit()
        yield session


def test_fetch_tv_detail_uses_base_record_for_facts_and_localized_text():
    client = FakeLocaleClient()

    details, _, _ = _fetch_tmdb_detail(
        client, media_type="tv", tmdb_id=307853, language="zh-CN", poster_language="zh-CN"
    )

    assert details is not None
    # 剧集事实字段（播出日期 / 时长 / 播出进度）取基础记录，和 TMDB 网站一致
    assert details["last_air_date"] == "2026-11-06"
    season = details["seasons_full"][0]
    episodes = {int(ep["episode_number"]): ep for ep in season["episodes"]}
    assert episodes[6]["air_date"] == "2026-11-07"
    assert episodes[6]["runtime"] == 46
    # 名称 / 简介仍然用配置语言的那份
    assert episodes[6]["name"] == "第 6 集"
    assert episodes[6]["overview"] == "本地化简介"
    # 确实请求过基础记录
    assert any(call[2] == BASE_LANGUAGE for call in client.calls)


def test_season_needs_base_only_for_recent_or_latest_seasons():
    cutoff = date.today() - timedelta(days=45)
    old = {"episodes": [{"air_date": "2019-03-01"}, {"air_date": "2019-03-08"}]}
    missing = {"episodes": [{"air_date": None}]}
    recent = {"episodes": [{"air_date": (date.today() - timedelta(days=3)).isoformat()}]}

    assert _season_needs_base(old, is_latest=False, cutoff=cutoff) is False
    assert _season_needs_base(missing, is_latest=False, cutoff=cutoff) is True
    assert _season_needs_base(recent, is_latest=False, cutoff=cutoff) is True
    assert _season_needs_base(old, is_latest=True, cutoff=cutoff) is True


def test_explicit_language_is_not_overridden_by_client_default(monkeypatch):
    client = TMDBClient("test-key", language="zh-CN")
    seen: list[dict] = []

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"ok": True}

    def fake_get(url, params=None, timeout=None):
        seen.append(dict(params or {}))
        return _Response()

    monkeypatch.setattr(client.session, "get", fake_get)

    client.get_tv_season(1, 1, language=BASE_LANGUAGE)
    assert seen[-1]["language"] == BASE_LANGUAGE
    assert seen[-1]["api_key"] == "test-key"

    client.get_tv_season(1, 1)
    assert seen[-1]["language"] == "zh-CN"


def test_force_refresh_passes_force_to_sync_refresh(monkeypatch, db):
    calls: list[bool] = []

    def fake_refresh(db, *, media_type, tmdb_id, language, poster_language, force=False, existing_only=False, close_read_tx=False):
        calls.append(force)
        assert close_read_tx is False  # 默认调用方（执行器路径）不得结束调用方事务
        return None, {"forced": force}, [], []

    monkeypatch.setattr(tmdb_cache, "refresh_tmdb_detail_sync", fake_refresh)

    ok, details, _, _, _ = get_tmdb_detail_cached(db, media_type="tv", tmdb_id=307853, force_refresh=True)

    assert ok is True
    assert calls == [True]
    assert details == {"forced": True}


def test_purge_removes_never_accessed_expired_rows(db):
    old = datetime.now() - timedelta(days=120)
    db.add_all(
        [
            TMDBMediaCache(
                media_type="tv",
                tmdb_id=1,
                language="zh-CN",
                poster_language="zh-CN",
                payload_json="{}",
                fetched_at=old,
                expires_at=old,
                last_accessed_at=None,
            ),
            TMDBMediaCache(
                media_type="tv",
                tmdb_id=2,
                language="zh-CN",
                poster_language="zh-CN",
                payload_json="{}",
                fetched_at=datetime.now(),
                expires_at=datetime.now() + timedelta(days=1),
                last_accessed_at=None,
            ),
        ]
    )
    db.commit()

    assert purge_cold_cache(db, retention_days=60) == 1
    db.commit()

    remaining = db.execute(select(TMDBMediaCache)).scalars().all()
    assert [row.tmdb_id for row in remaining] == [2]


def test_display_tz_does_not_double_convert_local_naive_times():
    from app.api.routes import tmdb_cache as routes

    local = datetime(2026, 11, 7, 14, 0)
    out = routes._as_display_tz(local)

    assert out is not None
    # 库里是服务器本地时间（naive），不能当 UTC 再转一次，否则页面时间会偏 8 小时
    assert out.astimezone(routes._SERVER_TZ).replace(tzinfo=None) == local
