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


def test_off_day_skips_without_adapter_init_or_account_construction() -> None:
    from datetime import datetime
    from app.application.dto.task_run import TaskRunPlan
    from app.domain.task_run import TaskRunState
    from app.infrastructure.execution import task_run_stages

    engine = task_run_stages.PreparedTaskEngine()
    off_day = datetime(2026, 11, 16, 12, 0)  # Monday, task allows Wednesday only.
    plan = TaskRunPlan(
        task=TaskRunState(id=7, task_uid="off", task_type="drama", taskname="Show", shareurl="https://pan.quark.cn/s/x", savepath="/show", enabled=True),
        prepared={
            "task_data": {
                "task_type": "drama",
                "taskname": "Show",
                "shareurl": "https://pan.quark.cn/s/x",
                "savepath": "/show",
                "extra": {"runweek_mode": "manual", "runweek": [3]},
                "schedule_now": off_day,
            },
            "share_check": None,
            "accounts": {"accounts": [{"name": "q1", "drive_type": "quark", "enabled": True}]},
        },
    )
    with patch.object(task_run_stages.DatabaseAccountManager, "from_payload") as from_payload:
        res = engine.execute(plan, RunPolicy.for_trigger("scheduler"), None, lambda *a: False)
        assert res.status == "skipped"
        assert res.stage == "validate_schedule"
        from_payload.assert_not_called()

    init_calls: list[str] = []
    adapter = SimpleNamespace(is_active=False, init=lambda: init_calls.append("init"))
    executor = TaskExecutor(db=None)
    with pytest.raises(SkipTask):
        executor._execute_adapter(
            plan.task,
            {**plan.prepared["task_data"], "manual_run": False},
            adapter,
            ExecutionLog(),
            False,
            plan.prepared["task_data"],
        )
    assert init_calls == []


def test_baidu_batch_rename_and_transient_retry() -> None:
    attempts = 0
    batch_calls: list[list[tuple[str, str]]] = []

    def _batch(pairs: list[tuple[str, str]]) -> dict[str, object]:
        batch_calls.append(pairs)
        return {"code": 0}

    def _rename(fid: str, name: str) -> dict[str, object]:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return {"status": 503, "message": "service unavailable"}
        return {"code": 0}

    baidu = object.__new__(BaiduAdapter)
    baidu.DRIVE_TYPE = "baidu"
    baidu.rename_batch = _batch  # type: ignore[method-assign]
    baidu.rename = _rename  # type: ignore[method-assign]
    baidu.ls_dir_complete = lambda dest: {  # type: ignore[method-assign]
        "code": 0,
        "data": {"list": [{"fs_id": "1", "file_name": "E01.mp4"}, {"fs_id": "2", "file_name": "E02.mp4"}]},
    }
    ex = DramaTaskExecutor(adapter=baidu, task_data={"task_type": "drama", "taskname": "S", "shareurl": "s", "savepath": "/show"}, log=ExecutionLog())
    with patch("app.infrastructure.execution.retry_utils.time.sleep"):
        ok, _ = ex._rename_by_fids(
            saved_fids=["1", "2"],
            plan=[
                SimpleNamespace(origin_name="a.mp4", target_name="E01.mp4"),  # type: ignore[list-item]
                SimpleNamespace(origin_name="b.mp4", target_name="E02.mp4"),  # type: ignore[list-item]
            ],
            dest_root_fid="/show",
        )
        assert ok is True
        assert batch_calls == [[("/show/a.mp4", "E01.mp4"), ("/show/b.mp4", "E02.mp4")]]
        assert ex._rename_one(fid="1", origin="a.mp4", target="E01.mp4", destination="/show") is True
        assert attempts == 2


def test_prune_task_executions_preserves_effective_history_under_skipped_flood(tmp_path: Path) -> None:
    from datetime import datetime, timedelta
    from sqlalchemy import select
    from app.application.task_executions import prune_task_executions
    from app.infrastructure.persistence.models.task_execution import TaskExecution

    engine, sessions = _make_session(tmp_path, "prune_skipped.db")
    try:
        base = datetime(2026, 1, 1, 0, 0, 0)
        with sessions.begin() as db:
            db.add(Task(id=1, task_uid="p1", taskname="p1", task_type="drama", shareurl="https://pan.quark.cn/s/1", savepath="/s"))
            for i in range(3):
                db.add(TaskExecution(task_id=1, status="success" if i != 1 else "failed", started_at=base + timedelta(hours=i), run_log="eff"))
            for i in range(10):
                db.add(TaskExecution(task_id=1, status="skipped", started_at=base + timedelta(hours=10 + i), run_log="skip"))
        with sessions.begin() as db:
            removed = prune_task_executions(db, 1, keep=6)
            assert removed == 7
            statuses = list(db.execute(select(TaskExecution.status).where(TaskExecution.task_id == 1)).scalars().all())
            assert len(statuses) == 6
            assert sum(1 for s in statuses if s != "skipped") == 3
    finally:
        engine.dispose()


def test_permanent_share_failure_does_not_retry_stoken_or_detail() -> None:
    stoken_calls = 0
    quark = object.__new__(QuarkAdapter)
    quark.DRIVE_TYPE = "quark"
    quark.extract_url = lambda url: ("pwd", "", "", None)  # type: ignore[method-assign]

    def _stoken(pwd: str, pc: str = "") -> dict[str, object]:
        nonlocal stoken_calls
        stoken_calls += 1
        return {"code": 41008, "message": "分享已失效"}

    quark.get_stoken = _stoken  # type: ignore[method-assign]
    ex = DramaTaskExecutor(adapter=quark, task_data={"task_type": "drama", "taskname": "S", "shareurl": "https://pan.quark.cn/s/x", "savepath": "/s"}, log=ExecutionLog())
    with patch("app.infrastructure.execution.drama_executor.settings.drama_runtime_retry_max_attempts", 3):
        with pytest.raises(ShareSourceInvalidError):
            ex.execute()
    assert stoken_calls == 1

    detail_calls = 0

    def _detail(*args: object) -> dict[str, object]:
        nonlocal detail_calls
        detail_calls += 1
        return {"code": 500, "message": "Cookie 认证失败"}

    quark.get_detail = _detail  # type: ignore[method-assign]
    with patch("app.infrastructure.execution.drama_executor.settings.drama_runtime_retry_max_attempts", 3):
        with pytest.raises(ShareAccessError):
            ex._fetch_share_detail(pwd_id="pwd", stoken="st", pdir_fid="")
    assert detail_calls == 1


def test_folder_diff_autoupdate_picks_best_branch_by_score() -> None:
    from app.application.drama_share_autoupdate import _pick_fid_for_folder_diff
    from app.application.share_listing import ShareFileGroup

    groups = [
        ShareFileGroup(files=[{"updated_at": 100}], fid="s1", updated_at=100, name="1080p", branch=("b_small", "TestShow"), priority=False),
        ShareFileGroup(files=[{"updated_at": 200}, {"updated_at": 300}], fid="s2", updated_at=300, name="4K", branch=("b_best", "TestShow"), priority=True),
        ShareFileGroup(files=[{"updated_at": 500}] * 5, fid="s3", updated_at=500, name="4K", branch=("b_other", "Other"), priority=True),
    ]
    assert _pick_fid_for_folder_diff(groups, "TestShow") == "b_best"


def test_scope_accounts_payload_filters_to_needed_accounts() -> None:
    from app.infrastructure.execution.task_run_stages import scope_accounts_payload

    payload = {
        "accounts": [
            {"name": "q1", "drive_type": "quark"},
            {"name": "b1", "drive_type": "baidu"},
            {"name": "x1", "drive_type": "xunlei"},
        ]
    }
    scoped = scope_accounts_payload(
        payload,
        task_data={"account_name": "q1", "shareurl": "https://pan.quark.cn/s/abc"},
        share_check={"record": {"share_url": "https://pan.baidu.com/s/xyz", "account_name": ""}},
    )
    assert [a["name"] for a in scoped["accounts"]] == ["q1", "b1"]

    unscoped = scope_accounts_payload(payload, task_data={"account_name": "", "shareurl": "unknown://url"}, share_check=None)
    assert unscoped == payload
