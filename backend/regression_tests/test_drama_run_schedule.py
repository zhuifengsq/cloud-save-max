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

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.api.routes.tasks import get_tasks
from app.api.routes.tasks_read import detail as get_task_detail, page as get_task_page
from app.application import drama_schedule, drama_update_progress
from app.application.container import run_task_use_case
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
from app.application.notifications.task_batch import TaskNotificationBatch
from app.application.queries.task_views import task_detail_view
from app.application.use_cases.run_task import RunPolicy
from app.core.runtime.clock import SystemClock
from app.domain.task_run import RunTaskCommand
from app.infrastructure.execution import drama_executor, task_run_stages
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.models.task_execution import TaskExecution
from app.infrastructure.persistence.models.task_scheduler_setting import TaskSchedulerSetting
from app.infrastructure.persistence.models.tmdb_media_cache import TMDBMediaCache
from app.infrastructure.scheduling import task_scheduler
from app.schemas.task_reads import TaskSummaryOut

# 2026-11-10 是周二（isoweekday()==2），用它固定「今天」避免测试随运行日漂移。
TUESDAY = datetime(2026, 11, 10, 9, 0)
WEDNESDAY_ONLY = datetime(2026, 11, 11, 9, 0)
WEDNESDAY_IN_SHANGHAI = datetime(2026, 11, 24, 16, 5, tzinfo=timezone.utc)


@pytest.fixture
def schedule_sessions(monkeypatch, tmp_path):
    class UtcHostDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return WEDNESDAY_IN_SHANGHAI.replace(tzinfo=None)
            return WEDNESDAY_IN_SHANGHAI.astimezone(tz)

    for module in (drama_schedule, drama_executor, drama_update_progress):
        monkeypatch.setattr(module, "datetime", UtcHostDatetime)
    monkeypatch.setattr(SystemClock, "now_utc", lambda self: WEDNESDAY_IN_SHANGHAI.replace(tzinfo=None))
    engine = create_engine(f"sqlite:///{(tmp_path / 'schedule.db').as_posix()}")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


def _wednesday_task(*, allow_once=False, **values):
    return Task(
        task_uid="wednesday-task", task_type="drama", taskname="Show", enabled=True,
        shareurl="https://pan.quark.cn/s/example", savepath="/show",
        extra_json=json.dumps({"runweek_mode": "manual", "runweek": [3], "allow_once": allow_once}),
        **values,
    )


@pytest.fixture
def empty_share_calls(monkeypatch):
    calls = []

    def fetch_share(pwd_id, stoken, fid):
        calls.append((pwd_id, stoken, fid))
        return {"code": 0, "data": {"list": []}}

    adapter = SimpleNamespace(
        is_active=True, DRIVE_TYPE="test", nickname="mock",
        extract_url=lambda url: ("example", "", "", []),
        get_stoken=lambda *args: {"data": {"stoken": "token"}},
        get_fids=lambda paths: [{"file_path": paths[0], "fid": "dest"}],
        get_detail=fetch_share,
        ls_dir=lambda *args, **kwargs: {"code": 0, "data": {"list": []}},
        close=lambda: None,
    )
    manager = SimpleNamespace(init_for_tasks=lambda tasks: None, get_adapter_for_task=lambda task: adapter)
    monkeypatch.setattr(task_run_stages.DatabaseAccountManager, "from_payload", lambda *args: manager)
    return calls


@pytest.mark.parametrize(
    "zone_name, expected_zone, status, reads, allow_once, remains_enabled",
    [
        ("Asia/Shanghai", "Asia/Shanghai", "success", 1, False, True),
        ("UTC", "UTC", "skipped", 0, False, True),
        (None, "Asia/Shanghai", "success", 1, False, True),
        ("UTC", "UTC", "success", 1, True, True),
    ],
)
def test_cron_weekday_uses_trigger_timezone(
    monkeypatch, schedule_sessions, empty_share_calls, zone_name, expected_zone,
    status, reads, allow_once, remains_enabled,
):
    monkeypatch.setattr(task_scheduler, "SessionLocal", schedule_sessions)
    notifier = SimpleNamespace(send=lambda *args, **kwargs: None)
    monkeypatch.setattr(task_scheduler, "task_run_notification_batch", lambda: TaskNotificationBatch(notifier))
    with schedule_sessions() as db:
        task = _wednesday_task(allow_once=allow_once)
        db.add(task)
        db.commit()
        task_id = task.id

    manager = task_scheduler.TaskSchedulerManager()
    manager.scheduler = BackgroundScheduler()
    manager._apply_setting(SimpleNamespace(enabled=True, crontab="5 0 * * *", timezone=zone_name))
    job = manager.scheduler.get_job("drama_tasks")
    assert str(job.trigger.timezone) == expected_zone
    job.func(*job.args, **job.kwargs)

    with schedule_sessions() as db:
        execution = db.scalars(select(TaskExecution).where(TaskExecution.task_id == task_id)).one()
        assert execution.status == status, execution.run_log
        assert db.get(Task, task_id).enabled is remains_enabled
    assert empty_share_calls == [("example", "token", "")] * reads


def test_manual_run_still_ignores_off_day(schedule_sessions, empty_share_calls):
    with schedule_sessions() as db:
        task = _wednesday_task()
        db.add_all([task, TaskSchedulerSetting(id=1, timezone="UTC")])
        db.commit()
        result = run_task_use_case(db).execute(RunTaskCommand(task.id), RunPolicy.for_trigger("http"))
        assert result.status == "success", result.run_log
    assert empty_share_calls == [("example", "token", "")]


def test_manual_run_still_blocks_missing_tmdb_binding(schedule_sessions, empty_share_calls):
    with schedule_sessions() as db:
        task = _wednesday_task()
        task.extra_json = '{"runweek_mode":"auto"}'
        db.add(task)
        db.commit()
        result = run_task_use_case(db).execute(RunTaskCommand(task.id), RunPolicy.for_trigger("http"))
        assert result.status == "skipped", result.run_log
        assert "未绑定 TMDB TV" in result.run_log
    assert empty_share_calls == []


@pytest.mark.parametrize("zone_name, reason, latest_episode", [
    ("Asia/Shanghai", REASON_DUE, 10),
    ("UTC", REASON_OFF_DAY, 9),
    (None, REASON_DUE, 10),
    ("Invalid/Legacy", REASON_DUE, 10),
])
def test_task_list_and_detail_use_scheduler_day(schedule_sessions, zone_name, reason, latest_episode):
    with schedule_sessions() as db:
        task = _wednesday_task(tmdb_id=99, tmdb_media_type="tv")
        db.add_all([task, TMDBMediaCache(media_type="tv", tmdb_id=99, payload_json=json.dumps({
            "last_episode_to_air": {"season_number": 1, "episode_number": 9, "air_date": "2026-11-24"},
            "next_episode_to_air": {"season_number": 1, "episode_number": 10, "air_date": "2026-11-25"},
        }))])
        if zone_name is not None:
            db.add(TaskSchedulerSetting(id=1, timezone=zone_name))
        db.commit()
        task_id = task.id

    with schedule_sessions() as db:
        current_page = get_task_page(page=1, page_size=20, conditions={"task_type": "drama"}, db=db)
        listing = TaskSummaryOut.model_validate(current_page["items"][0])
        detail = TaskSummaryOut.model_validate(get_task_detail(task_id, db))
        legacy = get_tasks(db)[0]
        edit_view = task_detail_view(db, db.get(Task, task_id))
        for view in (listing, detail, legacy, edit_view):
            assert (view.run_schedule.reason, view.drama_update_progress.tmdb_episode) == (reason, latest_episode)
            assert view.run_schedule.message == listing.run_schedule.message


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
