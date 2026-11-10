# -*- coding: utf-8 -*-
"""追剧任务「运行日期」判定：执行器 / 任务列表共用同一口径。

需求来源有两次变更：
1. 用户先说「不到设定的日期不能点击执行」→ 曾做过按钮禁用 + 409 拦截；
2. 用户随后澄清（m07014）「我的意思是希望不到设定的日期也能点击执行，并且执行」→
   手动执行必须**无视运行星期**：按钮永远可点、路由不 409、执行器不 SkipTask；
   运行星期只约束 APScheduler 定时轮询（manual_run=False）。

本文件锁死三件事：
1. 判定口径（resolve_run_schedule 的 reason / message / skip_reason）；
2. 手动越权范围（manual_override_allows：只越过日期类拦截，不越过配置缺失）；
3. 接线（RunPolicy 真值表的 manual_run、TaskExecutor 注入 task_data、drama_executor 放行分支、
   路由层不再有 ensure_run_allowed）。

改造前该逻辑内嵌在执行器里、无任何测试覆盖。
"""
from __future__ import annotations

from datetime import datetime

import pytest

from app.application.drama_schedule import (
    REASON_ALLOW_ONCE,
    REASON_AUTO_NOT_TV,
    REASON_DUE,
    REASON_INVALID_RUNWEEK,
    REASON_NOT_DRAMA,
    REASON_NO_RUNWEEK,
    REASON_NO_UPDATE_DAY,
    REASON_OFF_DAY,
    REASON_TMDB_NOT_CONFIGURED,
    manual_override_allows,
    normalize_weekdays,
    requires_skip,
    resolve_item_run_schedule,
    resolve_run_schedule,
    weekday_label,
)

# 2026-11-10 是周二（isoweekday()==2），用它固定「今天」避免测试随运行日漂移。
TUESDAY = datetime(2026, 11, 10, 9, 0)
WEDNESDAY_ONLY = datetime(2026, 11, 11, 9, 0)


def _manual(runweek, *, now=TUESDAY):
    return resolve_run_schedule(
        task_type="drama",
        extra={"runweek_mode": "manual", "runweek": runweek},
        now=now,
    )


def _auto(*, now=TUESDAY, tmdb_id=42, media_type="tv", configured=True, episodes=None, updates=None):
    return resolve_run_schedule(
        task_type="drama",
        extra={"runweek_mode": "auto"},
        now=now,
        tmdb_id=tmdb_id,
        tmdb_media_type=media_type,
        tmdb_configured=configured,
        episode_weekdays=episodes,
        update_weekdays=updates,
    )


def test_normalize_weekdays_is_tolerant_and_ordered():
    assert normalize_weekdays([3, 1, 3, 0, 8, "2"]) == (1, 2, 3)
    assert normalize_weekdays(None) == ()
    assert normalize_weekdays("123") == ()
    assert normalize_weekdays(["x", None, 5]) == (5,)
    assert weekday_label([1, 3]) == "周一、周三"


def test_non_drama_task_is_never_blocked():
    d = resolve_run_schedule(task_type="transfer", extra={}, now=TUESDAY)
    assert d.allowed is True
    assert d.reason == REASON_NOT_DRAMA


def test_allow_once_ignores_runweek():
    d = resolve_run_schedule(
        task_type="drama",
        extra={"allow_once": True, "runweek_mode": "manual", "runweek": [5]},
        now=TUESDAY,
    )
    assert d.allowed is True
    assert d.reason == REASON_ALLOW_ONCE


def test_manual_without_runweek_runs_every_day():
    d = _manual([])
    assert d.allowed is True
    assert d.reason == REASON_NO_RUNWEEK
    assert d.mode == "manual"


def test_manual_hit_and_miss():
    hit = _manual([2, 5])
    assert hit.allowed is True
    assert hit.reason == REASON_DUE
    assert hit.weekdays == (2, 5)
    assert "周二" in hit.message

    miss = _manual([5])
    assert miss.allowed is False
    assert miss.reason == REASON_OFF_DAY
    assert miss.skip_reason == "不在允许运行的星期范围内"
    assert "周五" in miss.message


def test_manual_invalid_values_block_execution():
    d = _manual([0, 9])
    assert d.allowed is False
    assert d.reason == REASON_INVALID_RUNWEEK
    assert d.skip_reason == "未配置运行星期"


def test_auto_requires_tv_binding_and_tmdb_config():
    not_tv = _auto(media_type="movie")
    assert not_tv.allowed is False
    assert not_tv.reason == REASON_AUTO_NOT_TV
    assert not_tv.skip_reason == "未绑定 TMDB TV"

    no_id = _auto(tmdb_id=0)
    assert no_id.reason == REASON_AUTO_NOT_TV

    unconfigured = _auto(configured=False)
    assert unconfigured.allowed is False
    assert unconfigured.reason == REASON_TMDB_NOT_CONFIGURED


def test_auto_without_update_day_runs_every_day():
    d = _auto(episodes=[], updates=[])
    assert d.allowed is True
    assert d.reason == REASON_NO_UPDATE_DAY


def test_auto_prefers_episode_weekdays_then_falls_back():
    hit = _auto(episodes=[], updates=[2])
    assert hit.allowed is True
    assert hit.weekdays == (2,)

    episode_wins = _auto(episodes=[3], updates=[2])
    assert episode_wins.weekdays == (3,)
    assert episode_wins.allowed is False
    assert episode_wins.reason == REASON_OFF_DAY

    miss = _auto(episodes=[6])
    assert miss.allowed is False
    assert miss.reason == REASON_OFF_DAY


def test_manual_override_only_bypasses_date_reasons():
    """手动执行的越权范围：日期类（不在运行日 / 星期配置无效）放行；配置缺失仍然拦。"""
    assert manual_override_allows(_manual([2])) is True  # 本来就允许
    off_day = _manual([5])
    assert off_day.allowed is False
    assert manual_override_allows(off_day) is True
    invalid = _manual([0, 9])
    assert invalid.allowed is False
    assert manual_override_allows(invalid) is True
    # 配置类问题不放行：放行也只会空跑
    assert manual_override_allows(_auto(media_type="movie")) is False
    assert manual_override_allows(_auto(tmdb_id=0)) is False
    assert manual_override_allows(_auto(configured=False)) is False


def test_requires_skip_honours_manual_run():
    """执行器唯一跳过口径：定时轮询按运行星期跳过，手动执行只跳过配置类问题。"""
    off_day = _manual([5])
    assert requires_skip(off_day, manual_run=False) is True
    assert requires_skip(off_day, manual_run=True) is False

    invalid = _manual([0, 9])
    assert requires_skip(invalid, manual_run=True) is False

    due = _manual([2])
    assert requires_skip(due, manual_run=False) is False
    assert requires_skip(due, manual_run=True) is False

    # 配置缺失：手动执行也跳（放行只会空跑）
    assert requires_skip(_auto(media_type="movie"), manual_run=True) is True
    assert requires_skip(_auto(configured=False), manual_run=True) is True


def test_item_level_schedule_matches_pure_judgement():
    """任务行判定（列表/详情用）与纯判定口径一致，且 manual 分支不碰数据库。"""
    due = resolve_item_run_schedule(None, _FakeTask(runweek=[2]), now=TUESDAY)
    assert due.allowed is True and due.reason == REASON_DUE
    off = resolve_item_run_schedule(None, _FakeTask(runweek=[5]), now=TUESDAY)
    assert off.allowed is False and off.reason == REASON_OFF_DAY


class _FakeTask:
    """仅提供判定需要的字段（不依赖 ORM/Session）。"""

    def __init__(self, *, runweek, task_type="drama", extra=None):
        import json

        self.id = 1
        self.task_type = task_type
        self.tmdb_id = 0
        self.tmdb_media_type = None
        self.extra_json = json.dumps(
            extra if extra is not None else {"runweek_mode": "manual", "runweek": runweek},
            ensure_ascii=False,
        )


def test_manual_run_is_wired_through_and_routes_do_not_block():
    """手动执行链路：RunPolicy -> TaskExecutor 注入 task_data -> drama_executor 放行；路由不得拦截。"""
    from pathlib import Path

    from app.application.use_cases.run_task import RunPolicy

    repo = Path(__file__).resolve().parents[2]
    app_dir = repo / "backend" / "app"
    plain = (app_dir / "api" / "routes" / "tasks.py").read_text(encoding="utf-8")
    stream = (app_dir / "api" / "routes" / "tasks_stream.py").read_text(encoding="utf-8")
    executor = (app_dir / "infrastructure" / "execution" / "task_executor.py").read_text(encoding="utf-8")
    drama = (app_dir / "infrastructure" / "execution" / "drama_executor.py").read_text(encoding="utf-8")

    # 1) 路由层：手动执行不再按运行星期拦截
    assert "ensure_run_allowed" not in plain
    assert "ensure_run_allowed" not in stream
    assert "TASK_RUN_NOT_SCHEDULED" not in plain
    assert "TASK_RUN_NOT_SCHEDULED" not in stream

    # 2) 策略真值表：只有 scheduler 是「非手动」
    assert RunPolicy.for_trigger("scheduler").manual_run is False
    for trigger in ("http", "sse", "telegram", "preview"):
        assert RunPolicy.for_trigger(trigger).manual_run is True, trigger

    # 3) 执行器把标记交给 drama 执行器，drama 执行器只在手动触发时跳过日期类拦截
    assert 'task_data["manual_run"] = bool(manual_run)' in executor
    assert "manual_run = bool(self.task_data.get(\"manual_run\"))" in drama
    assert "elif requires_skip(decision, manual_run):" in drama
    assert "手动执行: 忽略运行日期限制" in drama
