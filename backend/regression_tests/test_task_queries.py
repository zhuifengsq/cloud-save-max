# -*- coding: utf-8 -*-
"""任务读模型查询（application/queries）的批量性与值对象契约（S5-3）。

为什么需要它：路由原来自己编排「任务 + 最近执行记录 + TMDB 状态/payload + 引流摘要」
四个查询再逐条拼装，改造后收在 list_task_views() 一处。这类重构最容易悄悄退化成
N+1（每个任务再单独查一次 TMDB / 引流），或者在视图里塞回 ORM 对象导致懒加载复燃。
本测试用 SQL 语句计数 + 冻结断言把这两件事固定住。
"""
from __future__ import annotations

import dataclasses
from datetime import datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.application.queries.task_views import (
    execution_view,
    list_task_views,
    task_detail_view,
    task_view,
)
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.models.task_execution import TaskExecution


def _session(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'queries.db').as_posix()}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)(), engine


def _add_task(db: Session, index: int, *, executions: int = 5) -> Task:
    task = Task(
        task_uid=f"uid-{index}",
        task_type="transfer",
        taskname=f"task-{index}",
        shareurl=f"https://pan.quark.cn/s/{index}",
        savepath="/剧集",
        ignore_extension=False,
        enabled=True,
        addition_json='{"mode": "x"}',
        extra_json="{}",
    )
    db.add(task)
    db.flush()
    for k in range(executions):
        db.add(
            TaskExecution(
                task_id=task.id,
                status="success",
                started_at=datetime(2026, 11, 1, 10, k),
                tree_summary=f"s{index}-{k}",
                message=None,
                stage="transfer",
                run_log="",
                adapter_snapshot='{"drive": "quark"}' if k == 0 else None,
            )
        )
    db.commit()
    return task


def _record_selects(engine) -> list[str]:
    seen: list[str] = []

    def _record(conn, cursor, statement, parameters, context, executemany):
        text = statement.strip()
        if not text:
            return
        head = text.split(None, 1)[0].upper()
        if head in ("SELECT", "WITH"):
            seen.append(text)

    event.listen(engine, "before_cursor_execute", _record)
    return seen


def test_list_task_views_query_count_is_independent_of_task_count(tmp_path):
    """列表查询条数必须与任务数无关（禁止 N+1）。"""
    db, engine = _session(tmp_path)
    try:
        for i in range(1, 4):
            _add_task(db, i)
        db.expire_all()

        seen = _record_selects(engine)
        views = list_task_views(db)
        assert len(views) == 3
        small = len(seen)

        for i in range(4, 7):
            _add_task(db, i)
        db.expire_all()
        seen.clear()
        views = list_task_views(db)
        large = len(seen)
    finally:
        db.close()

    assert len(views) == 6
    assert large == small, f"查询条数随任务数增长（N+1）：3 个任务 {small} 条，6 个任务 {large} 条"
    assert large <= 8, f"列表查询条数过多（{large} 条），疑似退化成逐条查询"


def test_views_keep_all_executions_sorted_desc(tmp_path):
    """查询层负责排序（截断仍在 presenter，保持前端契约）。"""
    db, _ = _session(tmp_path)
    try:
        task = _add_task(db, 1, executions=5)
        db.expire_all()

        view = list_task_views(db)[0]
        minutes = [item.started_at.minute for item in view.executions]
        assert minutes == [4, 3, 2], "执行记录按 started_at 倒序"
        # 列表每任务最多 3 条来自仓储 limit=3；查询层不额外丢弃、也不补足。
        assert len(view.executions) == 3

        row = db.get(Task, task.id)
        # 显式传入 6 条时查询层全保留，截断只发生在 presenter（前端契约）。
        all_executions = db.query(TaskExecution).order_by(TaskExecution.started_at.desc()).all()
        all_view = task_view(None, row, executions=all_executions)
        assert len(all_view.executions) == 5
        assert all_view.executions[0].adapter_snapshot == {}, "最新一条没有快照"
        assert all_view.executions[-1].adapter_snapshot == {"drive": "quark"}, "adapter_snapshot 需解析成 dict"

        detail = task_detail_view(db, row)
        assert len(detail.executions) == 3, "详情固定最多 3 条执行记录"
        assert [item.started_at.minute for item in detail.executions] == [4, 3, 2]
    finally:
        db.close()


def test_views_are_frozen_value_objects(tmp_path):
    """视图是纯值对象：不可变，且不携带 ORM 实例状态（避免懒加载复燃）。"""
    db, _ = _session(tmp_path)
    try:
        task = _add_task(db, 1)
        db.expire_all()
        row = db.get(Task, task.id)
        view = task_view(None, row)

        assert not hasattr(view, "_sa_instance_state")
        assert view.addition == {"mode": "x"}
        assert view.lead_in == {"state": "none", "last_added_at": None}
        with pytest.raises(dataclasses.FrozenInstanceError):
            view.taskname = "changed"  # type: ignore[misc]
    finally:
        db.close()


def test_execution_view_tolerates_missing_snapshot(tmp_path):
    db, _ = _session(tmp_path)
    try:
        _add_task(db, 1, executions=1)
        db.expire_all()
        execution = db.query(TaskExecution).first()
        assert execution_view(execution).adapter_snapshot == {"drive": "quark"}
        assert execution_view(execution).status == "success"
        execution.adapter_snapshot = None
        assert execution_view(execution).adapter_snapshot == {}, "空快照按空字典"
        execution.adapter_snapshot = "not-json"
        assert execution_view(execution).adapter_snapshot == {}, "坏 JSON 按空字典容错（与改造前一致）"
    finally:
        db.close()
