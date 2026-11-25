"""Unit and regression tests for drama task reliability fixes (items 1-4)."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.application.use_cases.run_task import RunPolicy, RunTaskUseCase
from app.infrastructure.drive.adapters.baidu_adapter import BaiduAdapter
from app.infrastructure.drive.adapters.quark_adapter import QuarkAdapter
from app.infrastructure.drive.factory import AccountManager
from app.infrastructure.execution.drama_executor import (
    DramaTaskExecutor,
    ShareAccessError,
    ShareSourceInvalidError,
    SkipTask,
)
from app.infrastructure.execution.drama_plan import build_transfer_plan
from app.infrastructure.execution.execution_log import ExecutionLog
from app.infrastructure.execution.magic_rename import MagicRename
from app.infrastructure.execution.task_executor import TaskExecutor, _is_recoverable_share_failure
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.drama_transfer_checkpoint import DramaTransferCheckpoint
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.models.tmdb_media_cache import TMDBMediaCache
from app.infrastructure.persistence.repositories.tasks import stop_completed_drama_tasks


def _make_session(tmp_path: Path, name: str = "test.db"):
    engine = create_engine("sqlite:///" + (tmp_path / name).as_posix())
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine)


def test_complete_listing_fail_closed_across_normal_diff_and_rename() -> None:
    baidu = object.__new__(BaiduAdapter)
    baidu.DRIVE_TYPE = "baidu"
    baidu.ls_dir_complete = lambda *args, **kwargs: {"code": -1, "message": "truncated"}  # type: ignore[method-assign]
    baidu.get_fids = lambda paths: [{"fid": "dest-root", "file_path": "/show"}]  # type: ignore[method-assign]
    baidu.extract_url = lambda url: ("pwd", "", "", None)  # type: ignore[method-assign]
    baidu.get_stoken = lambda pwd, pc="": {"code": 0, "data": {"stoken": "ok"}}  # type: ignore[method-assign]

    for mode in ("normal", "folder_diff"):
        executor = DramaTaskExecutor(
            adapter=baidu,
            task_data={
                "task_type": "drama",
                "taskname": "TestShow",
                "shareurl": "https://pan.baidu.com/s/test",
                "savepath": "/show",
                "addition": {"folder_mode": mode},
            },
            log=ExecutionLog(),
        )
        with pytest.raises(SkipTask, match="无法完整读取目标目录"):
            executor.execute()

    calls: list[str] = []
    quark = object.__new__(QuarkAdapter)
    quark.DRIVE_TYPE = "quark"
    quark.ls_dir_complete = lambda fid: (calls.append(fid) or {"code": 0, "data": {"list": [{"fid": "f1", "file_name": "E01.mp4"}]}})  # type: ignore[method-assign]
    quark.ls_dir = lambda *args, **kwargs: pytest.fail("should use ls_dir_complete in _read_rename_names")  # type: ignore[method-assign]

    q_exec = DramaTaskExecutor(
        adapter=quark,
        task_data={"task_type": "drama", "taskname": "TestShow", "shareurl": "https://pan.quark.cn/s/abc", "savepath": "/show"},
        log=ExecutionLog(),
    )
    q_exec._quark_strategy = object()  # type: ignore[assignment]
    names = q_exec._read_rename_names("dest-1")
    assert calls == ["dest-1"]
    assert names is not None


def test_folder_diff_skipped_existing_folder_avoids_ls_dir_and_records_hint() -> None:
    ls_calls: list[str] = []
    adapter = SimpleNamespace(
        DRIVE_TYPE="quark",
        SUPPORTS_COMPLETE_LISTING=True,
        ls_dir=lambda fid, **kwargs: ls_calls.append(fid) or {"code": 0, "data": {"list": []}},
        ls_dir_complete=lambda fid: {"code": 0, "data": {"list": []}},
    )
    executor = DramaTaskExecutor(
        adapter=adapter,
        task_data={
            "task_type": "drama",
            "taskname": "TestShow",
            "shareurl": "https://pan.quark.cn/s/abc",
            "savepath": "/show",
            "addition": {"folder_mode": "folder_diff", "whitelist_folders": "4k"},
        },
        log=ExecutionLog(),
    )
    from treelib import Tree

    tree = Tree()
    tree.create_node("TestShow", "root")
    out = executor._save_share_dir_once(
        pwd_id="pwd",
        stoken="st",
        share_fid="share-ep12",
        share_token="tok",
        share_name="第12集",
        dest_root_fid="dest-root",
        dest_dir_map={"第12集": "dest-ep12"},
        savepath="/show",
        ignore_extension=False,
        tree=tree,
        dest_complete=True,
    )
    assert out == []
    assert ls_calls == []
    assert 12 in executor._folder_episode_hints


def test_account_manager_strict_named_account_rejects_fallback() -> None:
    mgr = AccountManager()
    quark_acc = SimpleNamespace(DRIVE_TYPE="quark", is_active=True)
    baidu_acc = SimpleNamespace(DRIVE_TYPE="baidu", is_active=True)
    inactive_quark = SimpleNamespace(DRIVE_TYPE="quark", is_active=False)
    mgr.adapters = {"q1": quark_acc, "b1": baidu_acc, "q_off": inactive_quark}  # type: ignore[assignment]

    assert mgr.get_adapter_for_task({"account_name": "missing", "shareurl": "https://pan.quark.cn/s/x"}) is None
    assert mgr.get_adapter_for_task({"account_name": "q_off", "shareurl": "https://pan.quark.cn/s/x"}) is None
    assert mgr.get_adapter_for_task({"account_name": "b1", "shareurl": "https://pan.quark.cn/s/x"}) is None
    assert mgr.get_adapter_for_task({"account_name": "q1", "shareurl": "https://pan.quark.cn/s/x"}) is quark_acc
    assert mgr.get_adapter_for_task({"account_name": "q_off", "shareurl": "https://pan.quark.cn/s/x"}, allow_inactive=True) is inactive_quark


def test_share_failure_classification_and_checkpoint_recovery_gate(tmp_path: Path) -> None:
    quark = object.__new__(QuarkAdapter)
    quark.DRIVE_TYPE = "quark"
    quark.extract_url = lambda url: ("pwd", "", "", None)  # type: ignore[method-assign]

    quark.get_stoken = lambda pwd, pc="": {"code": 41008, "message": "分享已失效"}  # type: ignore[method-assign]
    exec_invalid = DramaTaskExecutor(
        adapter=quark,
        task_data={"task_type": "drama", "taskname": "S", "shareurl": "https://pan.quark.cn/s/x", "savepath": "/s"},
        log=ExecutionLog(),
    )
    with patch("app.infrastructure.execution.retry_utils.settings.drama_runtime_retry_max_attempts", 1):
        with pytest.raises(ShareSourceInvalidError):
            exec_invalid.execute()

    quark.get_stoken = lambda pwd, pc="": {"code": 500, "message": "Cookie 认证失败"}  # type: ignore[method-assign]
    exec_auth = DramaTaskExecutor(
        adapter=quark,
        task_data={"task_type": "drama", "taskname": "S", "shareurl": "https://pan.quark.cn/s/x", "savepath": "/s"},
        log=ExecutionLog(),
    )
    with patch("app.infrastructure.execution.retry_utils.settings.drama_runtime_retry_max_attempts", 1):
        with pytest.raises(ShareAccessError) as auth_err:
            exec_auth.execute()
    assert auth_err.value.reason == "auth"

    baidu = object.__new__(BaiduAdapter)
    baidu.DRIVE_TYPE = "baidu"
    baidu.extract_url = lambda url: ("pwd", "", "", None)  # type: ignore[method-assign]
    baidu.get_stoken = lambda pwd, pc="": {"code": -7, "message": "分享正在审核"}  # type: ignore[method-assign]
    exec_pending = DramaTaskExecutor(
        adapter=baidu,
        task_data={"task_type": "drama", "taskname": "S", "shareurl": "https://pan.baidu.com/s/x", "savepath": "/s"},
        log=ExecutionLog(),
    )
    with patch("app.infrastructure.execution.retry_utils.settings.drama_runtime_retry_max_attempts", 1):
        with pytest.raises(SkipTask):
            exec_pending.execute()

    assert _is_recoverable_share_failure(ShareSourceInvalidError("expired"), "get_stoken") is True
    assert _is_recoverable_share_failure(ShareAccessError("auth", reason="auth"), "get_stoken") is False
    assert _is_recoverable_share_failure(RuntimeError("unknown"), "get_stoken") is False
    assert _is_recoverable_share_failure(TimeoutError("timeout"), "share_parse") is False
    assert _is_recoverable_share_failure(ValueError("bad shareurl"), "share_parse") is True

    engine, sessions = _make_session(tmp_path, "checkpoint_recover.db")
    try:
        with sessions.begin() as db:
            db.add(Task(id=1, task_uid="cp-1", taskname="cp-1", task_type="drama", shareurl="https://pan.quark.cn/s/old", savepath="/s", enabled=True))
            db.add(DramaTransferCheckpoint(task_id=1, scope="root", state_json="{}"))
        with sessions() as db:
            task = db.get(Task, 1)
            use_case = RunTaskUseCase(db)
            log = ExecutionLog()
            with patch("app.application.drama_share_autoupdate.resolve_drama_shareurl_update") as resolver:
                recovered = use_case._recover(task, log, set(), RunPolicy())
                assert recovered is False
                resolver.assert_not_called()
    finally:
        engine.dispose()


def test_stop_completed_drama_tasks_respects_progress_and_checkpoints(tmp_path: Path) -> None:
    engine, sessions = _make_session(tmp_path, "stop_completed.db")
    try:
        ended_s2_details = json.dumps({
            "seasons": [{"season_number": 2, "episodes": [{"episode_number": 10, "air_date": "2020-01-01"}]}],
            "last_episode_to_air": {"season_number": 2, "episode_number": 10, "air_date": "2020-01-01"},
        })
        with sessions.begin() as db:
            db.add(TMDBMediaCache(
                media_type="tv", tmdb_id=99, language="zh-CN", poster_language="zh-CN",
                status="Ended", payload_json=ended_s2_details,
            ))
            # Task 1: caught up & no checkpoint -> should stop
            db.add(Task(
                id=1, task_uid="t1", taskname="t1", task_type="drama", tmdb_id=99, tmdb_media_type="tv",
                shareurl="https://pan.quark.cn/s/1", savepath="/s1", enabled=True,
                extra_json=json.dumps({"saved_latest": {"season": 2, "episode": 10}}),
            ))
            # Task 2: missing saved season while TMDB is S2 -> should NOT stop
            db.add(Task(
                id=2, task_uid="t2", taskname="t2", task_type="drama", tmdb_id=99, tmdb_media_type="tv",
                shareurl="https://pan.quark.cn/s/2", savepath="/s2", enabled=True,
                extra_json=json.dumps({"saved_latest": {"season": None, "episode": 12}}),
            ))
            # Task 3: caught up in S2 but has active checkpoint -> should NOT stop
            db.add(Task(
                id=3, task_uid="t3", taskname="t3", task_type="drama", tmdb_id=99, tmdb_media_type="tv",
                shareurl="https://pan.quark.cn/s/3", savepath="/s3", enabled=True,
                extra_json=json.dumps({"saved_latest": {"season": 2, "episode": 10}}),
            ))
            db.add(DramaTransferCheckpoint(task_id=3, scope="root", state_json="{}"))

        with sessions.begin() as db:
            res = stop_completed_drama_tasks(db, language="zh-CN", poster_language="zh-CN")
            assert res == {"checked": 3, "matched_ids": [1], "stopped": 1}
        with sessions() as db:
            assert db.get(Task, 1).enabled is False
            assert db.get(Task, 2).enabled is True
            assert db.get(Task, 3).enabled is True
    finally:
        engine.dispose()


def test_allow_once_keeps_task_enabled_when_zero_transfers_and_disables_when_transferred() -> None:
    executor = TaskExecutor(db=None)
    task_zero = SimpleNamespace(id=1, enabled=True, task_type="drama", taskname="Show")
    task_transferred = SimpleNamespace(id=2, enabled=True, task_type="drama", taskname="Show")
    adapter = SimpleNamespace(is_active=True, nickname="acc", DRIVE_TYPE="quark")

    class ZeroDrama:
        def __init__(self, **kwargs):
            self.incomplete_directories = []
            self.deferred_directories = []
            self.transfer_count = 0

        def execute(self):
            from treelib import Tree

            t = Tree()
            t.create_node("Show", "root")
            return t

    class PositiveDrama(ZeroDrama):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.transfer_count = 2

    task_data = {"task_type": "drama", "taskname": "Show", "extra": {"allow_once": True}}
    with patch("app.infrastructure.execution.task_executor.DramaTaskExecutor", ZeroDrama):
        executor._execute_adapter(task_zero, task_data, adapter, ExecutionLog(), True, None)
    assert task_zero.enabled is True

    with patch("app.infrastructure.execution.task_executor.DramaTaskExecutor", PositiveDrama):
        executor._execute_adapter(task_transferred, task_data, adapter, ExecutionLog(), True, None)
    assert task_transferred.enabled is False


def test_magic_rename_sequential_numbering_in_plan_and_no_collision() -> None:
    mr = MagicRename()
    items = [
        {"fid": "a", "file_name_re": "Show - {II}.mp4", "updated_at": 100},
        {"fid": "b", "file_name_re": "Show - {II}.mp4", "updated_at": 100},
    ]
    mr.sort_file_list(items, start_index=1)
    assert [i["file_name_re"] for i in items] == ["Show - 01.mp4", "Show - 02.mp4"]

    plan = build_transfer_plan(
        {
            "taskname": "Show",
            "pattern": r".*\.mp4",
            "replace": "Show - E{II}.mp4",
            "ignore_extension": False,
        },
        filter_files=lambda c: c,
        share_files=[
            {"fid": "s1", "file_name": "clip_a.mp4", "updated_at": 200},
            {"fid": "s2", "file_name": "clip_b.mp4", "updated_at": 200},
        ],
        dest_file_list=[{"server_filename": "Show - E01.mp4", "isdir": 0}],
    )
    assert [item.target_name for item in plan] == ["Show - E02.mp4", "Show - E03.mp4"]
