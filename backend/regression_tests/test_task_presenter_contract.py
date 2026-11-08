# -*- coding: utf-8 -*-
"""TaskOut / TaskExecutionOut / TaskSchedulerSettingOut 的响应契约快照（S4 golden）。

为什么先有它再动序列化代码：presenter 从 services 下沉到 api/presenters 时最容易
「顺手改」隐式契约（executions 截断 3 条、字段顺序、JSON 字段解析、null 语义）。
本测试把一份固定输入的输出逐字段锁死，任何变化都必须先改这里的期望值并说明理由。

对应方案 §4.3；前端契约见 frontend/src/types/tasks.ts 与 frontend/src/api/http.ts。
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from app.api.presenters.task import execution_out, scheduler_out, task_out
from app.application.queries.task_views import execution_view, scheduler_view, task_view


def _execution(pid: int, started_at: datetime, *, adapter_snapshot: str | None = None):
    return SimpleNamespace(
        id=pid,
        task_id=7,
        status="success" if pid % 2 == 0 else "failed",
        started_at=started_at,
        finished_at=datetime(2026, 11, 1, 10, 30) if pid % 2 == 0 else None,
        tree_summary=f"summary-{pid}",
        message=None if pid % 2 == 0 else "boom",
        stage="transfer",
        run_log="line1\nline2",
        adapter_snapshot=adapter_snapshot,
    )


def _task():
    from app.infrastructure.persistence.models.task import Task

    item = Task(
        task_uid="uid-abc",
        task_type="transfer",
        taskname="测试任务",
        shareurl="https://pan.quark.cn/s/abc",
        savepath="/剧集",
        pattern=None,
        replace=None,
        ignore_extension=False,
        account_name="acc1",
        tmdb_id=None,
        tmdb_media_type=None,
        enabled=True,
        addition_json='{"mode": "x"}',
        extra_json='{"saved_latest": {"season": 1, "episode": 2}}',
    )
    item.id = 7
    item.created_at = datetime(2026, 10, 31, 9, 0)
    item.updated_at = datetime(2026, 11, 1, 9, 0)
    return item


def test_task_out_contract_is_frozen():
    item = _task()
    executions = [
        _execution(1, datetime(2026, 11, 1, 10, 0)),
        _execution(2, datetime(2026, 11, 1, 11, 0)),
        _execution(3, datetime(2026, 11, 1, 12, 0)),
        _execution(4, datetime(2026, 11, 1, 13, 0), adapter_snapshot='{"drive": "quark"}'),
    ]
    view = task_view(None, item, executions=executions, lead_in_map={7: {"state": "added", "last_added_at": "2026-11-01T00:00:00"}})
    out = task_out(view)

    # 字段顺序本身是 OpenAPI/前端快照的一部分，锁死。
    assert list(out.model_dump(mode="json").keys()) == [
        "id", "task_uid", "task_type", "taskname", "shareurl", "savepath",
        "pattern", "replace", "ignore_extension", "account_name",
        "tmdb_id", "tmdb_media_type", "tmdb_status", "tmdb_is_ended",
        "drama_update_progress", "lead_in", "enabled", "addition", "extra",
        "executions", "created_at", "updated_at",
    ]

    payload = out.model_dump(mode="json")
    assert payload == {
        "id": 7,
        "task_uid": "uid-abc",
        "task_type": "transfer",
        "taskname": "测试任务",
        "shareurl": "https://pan.quark.cn/s/abc",
        "savepath": "/剧集",
        "pattern": None,
        "replace": None,
        "ignore_extension": False,
        "account_name": "acc1",
        "tmdb_id": None,
        "tmdb_media_type": None,
        "tmdb_status": None,
        "tmdb_is_ended": None,
        "drama_update_progress": None,
        "lead_in": {"state": "added", "last_added_at": "2026-11-01T00:00:00"},
        "enabled": True,
        "addition": {"mode": "x"},
        "extra": {"saved_latest": {"season": 1, "episode": 2}},
        "executions": [
            {
                "id": 4,
                "task_id": 7,
                "status": "success",
                "started_at": "2026-11-01T13:00:00",
                "finished_at": "2026-11-01T10:30:00",
                "tree_summary": "summary-4",
                "message": None,
                "stage": "transfer",
                "run_log": "line1\nline2",
                "adapter_snapshot": {"drive": "quark"},
            },
            {
                "id": 3,
                "task_id": 7,
                "status": "failed",
                "started_at": "2026-11-01T12:00:00",
                "finished_at": None,
                "tree_summary": "summary-3",
                "message": "boom",
                "stage": "transfer",
                "run_log": "line1\nline2",
                "adapter_snapshot": {},
            },
            {
                "id": 2,
                "task_id": 7,
                "status": "success",
                "started_at": "2026-11-01T11:00:00",
                "finished_at": "2026-11-01T10:30:00",
                "tree_summary": "summary-2",
                "message": None,
                "stage": "transfer",
                "run_log": "line1\nline2",
                "adapter_snapshot": {},
            },
        ],
        "created_at": "2026-10-31T09:00:00",
        "updated_at": "2026-11-01T09:00:00",
    }


def test_task_out_truncates_executions_to_three():
    """前端固定只渲染最近 3 条（历史契约），多出来的必须被截断而不是分页。"""
    executions = [_execution(i, datetime(2026, 11, 1, 10 + i, 0)) for i in range(6)]
    out = task_out(task_view(None, _task(), executions=executions))
    assert [e.id for e in out.executions] == [5, 4, 3]


def test_task_out_defaults_when_lead_in_and_extras_missing():
    item = _task()
    item.addition_json = None
    item.extra_json = None
    out = task_out(task_view(None, item, executions=[]))
    payload = out.model_dump(mode="json")
    assert payload["addition"] == {}
    assert payload["extra"] == {}
    assert payload["executions"] == []
    assert payload["lead_in"] == {"state": "none", "last_added_at": None}


def test_execution_out_and_scheduler_out_contract():
    e = _execution(1, datetime(2026, 11, 1, 10, 0))
    assert execution_out(execution_view(e)).model_dump(mode="json") == {
        "id": 1,
        "task_id": 7,
        "status": "failed",
        "started_at": "2026-11-01T10:00:00",
        "finished_at": None,
        "tree_summary": "summary-1",
        "message": "boom",
        "stage": "transfer",
        "run_log": "line1\nline2",
        "adapter_snapshot": {},
    }
    sched = scheduler_out(scheduler_view(SimpleNamespace(enabled=True, crontab="0 8 * * *", timezone="Asia/Shanghai")))
    assert sched.model_dump(mode="json") == {"enabled": True, "crontab": "0 8 * * *", "timezone": "Asia/Shanghai"}
