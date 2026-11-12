# -*- coding: utf-8 -*-
"""追剧任务「分类」口径（app/application/media_category.py）。

为什么要有它：分类是**纯展示口径**，算错了不会报错、只会让任务悄悄跑到别的分类里
（甚至从筛选条里消失）。这里把十类口径、优先级、缺数据兜底和列表接线逐条锁死。

对应方案 §10.32。
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.application.media_category import (
    CATEGORY_ANIME,
    CATEGORY_CN,
    CATEGORY_HK_TW,
    CATEGORY_JP,
    CATEGORY_KR,
    CATEGORY_MOVIE,
    CATEGORY_OTHER_TV,
    CATEGORY_UNKNOWN,
    CATEGORY_US,
    CATEGORY_VARIETY,
    categorize,
)
from app.application.queries.task_views import task_view
from app.api.presenters.task import task_out


def _payload(*, genres=(), country=(), media_lang=None, produced=None):
    data = {"genres": [{"id": gid, "name": name} for gid, name in genres]}
    if country:
        data["origin_country"] = list(country)
    if produced is not None:
        data["production_countries"] = produced
    if media_lang is not None:
        data["original_language"] = media_lang
    return data


def test_genre_based_categories():
    assert categorize("tv", _payload(genres=[(16, "Animation")])) == CATEGORY_ANIME
    assert categorize("tv", _payload(genres=[(10764, "Reality")])) == CATEGORY_VARIETY
    assert categorize("tv", _payload(genres=[(10767, "Talk")])) == CATEGORY_VARIETY
    assert categorize("tv", _payload(genres=[(10763, "News")])) == CATEGORY_VARIETY
    assert categorize("tv", _payload(genres=[(18, "Drama")])) == CATEGORY_OTHER_TV


def test_genre_name_fallback_when_id_missing():
    """老缓存/异形 payload 可能没有 id，只靠类型名也要能认出来（前端 mediaCategory.ts 同口径）。"""
    no_id = {"genres": [{"name": "动画"}]}
    assert categorize("tv", no_id) == CATEGORY_ANIME
    assert categorize("tv", {"genres": [{"name": "Talk Show"}]}) == CATEGORY_VARIETY


def test_country_based_categories():
    assert categorize("tv", _payload(country=["CN"])) == CATEGORY_CN
    assert categorize("tv", _payload(country=["KR"])) == CATEGORY_KR
    assert categorize("tv", _payload(country=["JP"])) == CATEGORY_JP
    assert categorize("tv", _payload(country=["US"])) == CATEGORY_US
    assert categorize("tv", _payload(country=["HK"])) == CATEGORY_HK_TW
    assert categorize("tv", _payload(country=["TW"])) == CATEGORY_HK_TW
    assert categorize("tv", _payload(country=["GB"])) == CATEGORY_OTHER_TV


def test_priority_order_is_hit_and_stop():
    """动漫/综艺优先于国家；国家按 国剧>韩剧>日剧>美剧>港台剧。"""
    assert categorize("tv", _payload(genres=[(16, "Animation")], country=["JP"])) == CATEGORY_ANIME
    assert categorize("tv", _payload(genres=[(10764, "Reality")], country=["CN"])) == CATEGORY_VARIETY
    assert categorize("movie", _payload(genres=[(16, "Animation")], country=["US"])) == CATEGORY_ANIME
    assert categorize("tv", _payload(country=["CN", "HK"])) == CATEGORY_CN
    assert categorize("tv", _payload(country=["KR", "US"])) == CATEGORY_KR


def test_movie_is_movie_regardless_of_country():
    assert categorize("movie", _payload(country=["US"])) == CATEGORY_MOVIE
    assert categorize("movie", _payload()) == CATEGORY_MOVIE


def test_missing_payload_is_unknown_not_error():
    """缺数据只能落到「未分类」，绝不能抛异常（否则这个任务会从列表里消失）。"""
    assert categorize("tv", None) == CATEGORY_UNKNOWN
    assert categorize("tv", {}) == CATEGORY_UNKNOWN
    assert categorize("tv", "not-a-dict") == CATEGORY_UNKNOWN
    assert categorize(None, None) == CATEGORY_UNKNOWN
    assert categorize("tv", {"genres": ["oops", 3, None]}) == CATEGORY_OTHER_TV


def test_country_fallbacks():
    produced = [{"iso_3166_1": "KR", "name": "South Korea"}]
    assert categorize("tv", _payload(produced=produced)) == CATEGORY_KR
    # 只有语言时按 ko/ja/en 兜底；zh 不兜底（国剧/港台剧无法区分）
    assert categorize("tv", _payload(media_lang="ko")) == CATEGORY_KR
    assert categorize("tv", _payload(media_lang="ja")) == CATEGORY_JP
    assert categorize("tv", _payload(media_lang="zh")) == CATEGORY_OTHER_TV


def _drama_task(tmdb_media_type="tv", tmdb_id=279923):
    item = SimpleNamespace(
        id=11,
        task_uid="uid-drama",
        task_type="drama",
        taskname="追剧任务",
        shareurl="https://pan.quark.cn/s/abc",
        savepath="/剧集",
        pattern=None,
        replace=None,
        ignore_extension=False,
        account_name="acc1",
        tmdb_id=tmdb_id,
        tmdb_media_type=tmdb_media_type,
        enabled=True,
        addition_json=None,
        extra_json=None,
        executions=[],
        created_at=datetime(2026, 11, 1, 9, 0),
        updated_at=datetime(2026, 11, 1, 9, 0),
    )
    return item


def test_task_view_wires_category_from_batched_payload():
    """列表场景：payload 由 load_tmdb_payload_map 批量传入，分类不能额外查库。"""
    payload = _payload(genres=[(10764, "Reality")], country=["CN"])
    view = task_view(None, _drama_task(), executions=[], tmdb_payload_map={("tv", 279923): payload})
    assert view.media_category == CATEGORY_VARIETY
    assert task_out(view).media_category == CATEGORY_VARIETY


def test_task_view_without_payload_is_unknown():
    view = task_view(None, _drama_task(), executions=[], tmdb_payload_map={})
    assert view.media_category == CATEGORY_UNKNOWN


def test_non_drama_task_has_no_category():
    item = _drama_task()
    item.task_type = "transfer"
    view = task_view(None, item, executions=[])
    assert view.media_category is None
    assert task_out(view).media_category is None
