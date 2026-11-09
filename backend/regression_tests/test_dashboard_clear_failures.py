"""护栏：工作台「清理失败记录」后，已清理的分享链接检测失败不应再出现在「近期失败」。

背景：`recent_failures` 有两个来源——失败执行记录（清理会删掉）与**成功**执行记录
`adapter_snapshot.generated_share_check` 里的失效/异常状态（删不掉，只能靠清理水位屏蔽）。
若没有水位，用户点「清理失败日志」后仍会看到失败任务（2026-11-09 线上现象）。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.application.dashboard_drama import (
    DRAMA_FAILURES_CLEARED_KEY,
    build_drama_overview,
    clear_drama_failures,
    mark_failures_cleared,
    read_failures_cleared_at,
)
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.system_setting import SystemSetting
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.models.task_execution import TaskExecution
from app.infrastructure.persistence.models.task_scheduler_setting import TaskSchedulerSetting


def _open_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[
            Task.__table__,
            TaskExecution.__table__,
            TaskSchedulerSetting.__table__,
            SystemSetting.__table__,
        ],
    )
    return engine, Session(engine)


def _add_drama_task(db: Session, *, taskname: str = "测试剧") -> Task:
    task = Task(
        task_uid=f"uid-{taskname}",
        task_type="drama",
        taskname=taskname,
        shareurl="https://pan.quark.cn/s/source",
        savepath="/测试",
        enabled=True,
    )
    db.add(task)
    db.flush()
    return task


def _share_check_snapshot(*, checked_at: datetime, status: str = "invalid") -> str:
    # 与真实写入一致：checked_at 是带时区的本地 ISO 字符串
    payload = {
        "generated_share_check": {
            "status": status,
            "message": "分享链接已失效",
            "notify": False,
            "checked_at": checked_at.astimezone().isoformat(),
        }
    }
    return json.dumps(payload, ensure_ascii=False)


def _snapshot_status(raw: str | None) -> str | None:
    if not raw:
        return None
    return json.loads(raw).get("generated_share_check", {}).get("status")


def test_clear_hides_share_check_failure_stored_on_success_execution():
    engine, db = _open_session()
    now = datetime.now()
    try:
        task = _add_drama_task(db)
        db.add(
            TaskExecution(
                task_id=task.id,
                status="failed",
                started_at=now - timedelta(hours=1),
                finished_at=now - timedelta(hours=1) + timedelta(seconds=30),
                stage="end",
                message="转存失败",
            )
        )
        db.add(
            TaskExecution(
                task_id=task.id,
                status="success",
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=2) + timedelta(seconds=40),
                adapter_snapshot=_share_check_snapshot(checked_at=now - timedelta(hours=2)),
            )
        )
        db.commit()

        before = build_drama_overview(db)
        stages = sorted(str(row["stage"] or "") for row in before["recent_failures"])
        assert stages == ["end", "generated_share_check"], stages
        assert before["summary"]["execution_failed"] == 1

        deleted = clear_drama_failures(db, days=0)
        db.commit()
        assert deleted == 1
        assert read_failures_cleared_at(db) is not None

        after = build_drama_overview(db)
        assert after["recent_failures"] == []
        assert after["summary"]["execution_failed"] == 0
        assert after["summary"]["execution_success"] == 1

        # 清理只影响仪表盘展示：成功记录与快照原样保留，任务列表徽章仍如实显示「已失效」
        remaining = db.execute(select(TaskExecution)).scalars().all()
        assert len(remaining) == 1 and remaining[0].status == "success"
        assert _snapshot_status(remaining[0].adapter_snapshot) == "invalid"
    finally:
        db.close()
        engine.dispose()


def test_failure_newer_than_watermark_is_still_shown():
    engine, db = _open_session()
    now = datetime.now()
    try:
        task = _add_drama_task(db)
        db.add(
            TaskExecution(
                task_id=task.id,
                status="success",
                started_at=now - timedelta(hours=3),
                adapter_snapshot=_share_check_snapshot(checked_at=now - timedelta(hours=3)),
            )
        )
        db.commit()

        cutoff = now - timedelta(hours=1)
        clear_drama_failures(db, days=0, now=cutoff)
        db.commit()
        assert build_drama_overview(db)["recent_failures"] == []

        # 清理之后新发生的失败必须照常展示（水位只屏蔽历史，不屏蔽未来）
        db.add(
            TaskExecution(
                task_id=task.id,
                status="failed",
                started_at=now - timedelta(minutes=5),
                stage="end",
                message="转存失败（清理后新增）",
            )
        )
        db.add(
            TaskExecution(
                task_id=task.id,
                status="success",
                started_at=now - timedelta(minutes=2),
                adapter_snapshot=_share_check_snapshot(checked_at=now - timedelta(minutes=2)),
            )
        )
        db.commit()

        rows = build_drama_overview(db)["recent_failures"]
        assert len(rows) == 2, rows
        assert {str(row["stage"] or "") for row in rows} == {"end", "generated_share_check"}
    finally:
        db.close()
        engine.dispose()


def test_clear_with_days_only_hides_entries_before_cutoff():
    engine, db = _open_session()
    now = datetime.now()
    try:
        task = _add_drama_task(db)
        # 40 天前：失败记录与「成功记录里的检测失败」都应在 days=30 清理中消失
        db.add(
            TaskExecution(
                task_id=task.id,
                status="failed",
                started_at=now - timedelta(days=40),
                stage="end",
                message="40 天前的转存失败",
            )
        )
        db.add(
            TaskExecution(
                task_id=task.id,
                status="success",
                started_at=now - timedelta(days=40),
                adapter_snapshot=_share_check_snapshot(checked_at=now - timedelta(days=40)),
            )
        )
        # 10 天前：仍在 30 天窗口内，必须保留
        db.add(
            TaskExecution(
                task_id=task.id,
                status="failed",
                started_at=now - timedelta(days=10),
                stage="end",
                message="10 天前的转存失败",
            )
        )
        db.add(
            TaskExecution(
                task_id=task.id,
                status="success",
                started_at=now - timedelta(days=10),
                adapter_snapshot=_share_check_snapshot(checked_at=now - timedelta(days=10)),
            )
        )
        db.commit()

        assert len(build_drama_overview(db, days=60)["recent_failures"]) == 4

        # 清理最近 30 天之前的：40 天前的失败记录被删 + 水位屏蔽 40 天前的检测失败，10 天前的两条保留
        assert clear_drama_failures(db, days=30, now=now) == 1
        db.commit()

        rows = build_drama_overview(db, days=60)["recent_failures"]
        assert len(rows) == 2, rows
        assert {str(row["stage"] or "") for row in rows} == {"end", "generated_share_check"}
        for row in rows:
            checked = datetime.fromisoformat(str(row["started_at"]))
            assert checked.date() == (now - timedelta(days=10)).date()

        left = db.execute(select(TaskExecution)).scalars().all()
        assert len(left) == 3  # 40 天前的失败记录被删；两条 10 天前的 + 一条成功快照保留
    finally:
        db.close()
        engine.dispose()


def test_watermark_only_moves_forward():
    engine, db = _open_session()
    now = datetime.now()
    try:
        mark_failures_cleared(db, now)
        db.commit()
        first = read_failures_cleared_at(db)
        assert first is not None
        assert abs((first - now).total_seconds()) < 1

        # 先清理「最近 30 天前」再清理「全部」：水位不能被回拨，否则已隐藏的旧失败会重新冒出来
        mark_failures_cleared(db, now - timedelta(days=30))
        db.commit()
        assert read_failures_cleared_at(db) == first

        stored = db.execute(
            select(SystemSetting).where(SystemSetting.key == DRAMA_FAILURES_CLEARED_KEY)
        ).scalars().first()
        assert stored is not None
    finally:
        db.close()
        engine.dispose()
