"""UnitOfWork / transaction 的行为护栏（纯新增，不依赖真实库文件）。"""
from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.persistence.unit_of_work import UnitOfWork, transaction


def _session():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE uow_probe (id INTEGER PRIMARY KEY, name TEXT)"))
    return engine, Session(engine)


def _locked_error():
    return OperationalError("COMMIT", {}, sqlite3.OperationalError("database is locked"))


def test_commit_persists_rows():
    engine, db = _session()
    try:
        with transaction(db):
            db.execute(text("INSERT INTO uow_probe (name) VALUES ('a')"))
        with Session(engine) as check:
            assert check.execute(text("SELECT count(*) FROM uow_probe")).scalar_one() == 1
    finally:
        db.close()


def test_exception_rolls_back_rows():
    engine, db = _session()
    try:
        with pytest.raises(RuntimeError, match="boom"):
            with transaction(db):
                db.execute(text("INSERT INTO uow_probe (name) VALUES ('b')"))
                raise RuntimeError("boom")
        with Session(engine) as check:
            assert check.execute(text("SELECT count(*) FROM uow_probe")).scalar_one() == 0
    finally:
        db.close()


def test_unit_of_work_wrapper_commits():
    engine, db = _session()
    try:
        with UnitOfWork(db) as session:
            session.execute(text("INSERT INTO uow_probe (name) VALUES ('c')"))
        with Session(engine) as check:
            assert check.execute(text("SELECT count(*) FROM uow_probe")).scalar_one() == 1
    finally:
        db.close()


def test_locked_commit_retries_then_succeeds(monkeypatch):
    """事务仍存活（ACTIVE）时，commit 的锁冲突允许退避重试。"""
    import app.infrastructure.persistence.session as session_module

    monkeypatch.setattr(session_module.time, "sleep", lambda _seconds: None)
    engine, db = _session()
    try:
        db.execute(text("SELECT 1"))  # 先开一个真实事务
        real_commit = db.commit
        calls = {"n": 0}

        def flaky_commit():
            calls["n"] += 1
            if calls["n"] <= 2:
                raise _locked_error()
            real_commit()

        db.commit = flaky_commit  # type: ignore[method-assign]
        with transaction(db):
            db.execute(text("INSERT INTO uow_probe (name) VALUES ('retry')"))
        assert calls["n"] == 3
    finally:
        db.close()


def test_non_lock_error_is_not_retried(monkeypatch):
    import app.infrastructure.persistence.session as session_module

    monkeypatch.setattr(session_module.time, "sleep", lambda _seconds: None)
    engine, db = _session()
    try:
        db.execute(text("SELECT 1"))
        real_commit = db.commit
        calls = {"n": 0}

        def flaky_commit():
            calls["n"] += 1
            if calls["n"] == 1:
                raise OperationalError("COMMIT", {}, sqlite3.OperationalError("disk I/O error"))
            real_commit()

        db.commit = flaky_commit  # type: ignore[method-assign]
        with pytest.raises(OperationalError, match="disk I/O error"):
            with transaction(db):
                db.execute(text("INSERT INTO uow_probe (name) VALUES ('io')"))
        assert calls["n"] == 1
    finally:
        db.close()


def test_lock_conflict_is_not_masked(tmp_path):
    """写锁冲突发生在 flush 时：必须抛出锁错误，不能重试成「提交空事务」的假成功。

    旧实现（retry_on_locked + on_retry=db.rollback）会在第一次失败后回滚，
    再 commit 空事务并返回成功——接口 200、数据静默丢失。此用例锁死该行为。
    """
    from app.infrastructure.persistence.base import Base
    from app.infrastructure.persistence.models.task_template import TaskTemplate

    db_path = tmp_path / "lock_conflict.db"
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 0.05},
    )
    Base.metadata.create_all(engine)
    MakeSession = sessionmaker(bind=engine, autoflush=False, future=True)

    holder = sqlite3.connect(str(db_path), timeout=0.05)
    holder.execute("BEGIN IMMEDIATE")
    holder.execute(
        "INSERT INTO task_templates (name, config_json) VALUES ('holder', '{}')"
    )

    db = MakeSession()
    try:
        db.execute(text("SELECT 1"))
        with pytest.raises(OperationalError, match="database is locked"):
            with transaction(db):
                db.add(TaskTemplate(name="victim", config_json="{}"))
    finally:
        db.close()
        holder.rollback()
        holder.close()
        engine.dispose()

    check = sqlite3.connect(str(db_path))
    try:
        victim = check.execute(
            "SELECT count(*) FROM task_templates WHERE name='victim'"
        ).fetchone()[0]
    finally:
        check.close()
    assert victim == 0
