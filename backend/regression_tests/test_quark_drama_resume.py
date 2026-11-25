from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from treelib import Tree

from app.application.container import run_task_use_case
from app.application.use_cases.run_task import RunPolicy
from app.core.runtime.rate_limiter import KeyedRateLimiter
from app.domain.task_run import RunTaskCommand
from app.infrastructure.drive.adapters.quark_adapter import QuarkAdapter
from app.infrastructure.execution import task_run_stages
from app.infrastructure.execution.drama_executor import DramaTaskExecutor, SkipTask
from app.infrastructure.execution.drama_plan import DramaPlanItem
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.drama_transfer_checkpoint import DramaTransferCheckpoint
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.repositories.drama_transfers import DramaTransferStore


@pytest.mark.parametrize("remote_status", [1, "3"])
def test_quark_polling_stops_on_deadline_or_terminal_failure(monkeypatch, remote_status):
    adapter = object.__new__(QuarkAdapter)
    elapsed = 0.0
    attempts = []

    def sleep(seconds):
        nonlocal elapsed
        elapsed += seconds

    def send(*args, **kwargs):
        attempts.append(elapsed)
        status = remote_status if len(attempts) < 6 else 2
        return SimpleNamespace(json=lambda: {"code": 0, "status": 200, "data": {"status": status}})

    monkeypatch.setattr("time.monotonic", lambda: elapsed)
    monkeypatch.setattr("time.sleep", sleep)
    monkeypatch.setattr(adapter, "_send_request", send)
    monkeypatch.setattr(QuarkAdapter, "QUERY_TASK_TIMEOUT_SECONDS", 1, raising=False)

    result = adapter.query_task("remote-1")

    assert str(result["data"]["status"]) == str(remote_status)
    assert elapsed <= 1
    if remote_status == "3":
        assert attempts == [0.0]


def test_lookup_budget_does_not_reserve_a_rate_limit_slot_beyond_deadline():
    waits = []
    limiter = KeyedRateLimiter(clock=lambda: 0.0, sleeper=waits.append)
    limiter.wait("quark", 3)

    with pytest.raises(TimeoutError):
        limiter.wait("quark", 3, max_wait=1)

    assert waits == []
    assert limiter.next_allowed_at("quark") == 3


def test_quark_query_preserves_http_rejection(monkeypatch):
    adapter = object.__new__(QuarkAdapter)
    response = SimpleNamespace(status_code=401, json=lambda: {"code": 1, "message": "login required"})
    monkeypatch.setattr(adapter, "_send_request", lambda *args, **kwargs: response)

    assert adapter.query_task("remote-1") == {"code": 1, "message": "login required", "status": 401}


def test_quark_malformed_save_response_is_uncertain(monkeypatch):
    adapter = object.__new__(QuarkAdapter)
    response = SimpleNamespace(status_code=200, json=lambda: ["accepted"])
    monkeypatch.setattr(adapter, "_send_request", lambda *args, **kwargs: response)

    assert adapter.save_file(["source"], ["file-token"], "dest", "share", "share-token") == {"write_uncertain": True}


def test_recovery_actions_require_permission_and_current_checkpoint(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.deps import CurrentUser, get_current_user
    from app.api.routes import tasks
    from app.application.container import get_db
    from app.core.errors import ApiError
    from app.core.exception_handlers import api_error_handler
    from app.core.permissions import TASK_READ, TASK_WRITE
    from app.infrastructure.persistence.models.audit_log import AuditLog
    from app.infrastructure.persistence.repositories.drama_transfers import TransferBatch
    from app.infrastructure.scheduling.task_admission import TaskAdmission

    engine = create_engine("sqlite:///" + (tmp_path / "recovery.db").as_posix())
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        task = Task(task_uid="recovery", task_type="drama", taskname="show", shareurl="share", savepath="/show")
        db.add(task)
        db.commit()
        task_id = task.id
    store = DramaTransferStore(lambda: Session(engine), task_id)
    batch = TransferBatch("dest", [("source", "Episode.mkv", "E01.mkv")], task_id="job")
    store.begin_batch("scope", batch)
    for _ in range(3):
        store.record_problem("scope", "尚未取得文件 ID")

    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(tasks.router, prefix="/tasks")
    permissions = {TASK_READ}
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(SimpleNamespace(id=None), [], permissions)

    def session():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = session
    admission = TaskAdmission(max_active=1)
    monkeypatch.setattr(tasks, "task_admission", admission)
    url = f"/tasks/{task_id}/transfer-recovery"
    with TestClient(app) as client:
        snapshot = client.get(url).json()
        assert snapshot["requires_confirmation"] and snapshot["attempts"] == 3
        prev = snapshot["version"]
        assert client.post(url, json={"version": prev, "action": "retry"}).status_code == 403
        permissions.add(TASK_WRITE)
        with admission.reserve(task_id):
            assert client.post(url, json={"version": prev, "action": "retry"}).status_code == 409
        assert client.post(url, json={"version": prev, "action": "retry"}).status_code == 200
        active = store.load()
        assert active.batch["task_id"] == "job" and active.recovery == {}
        assert client.post(url, json={"version": prev, "action": "abandon", "confirm": True}).status_code == 409
        assert client.post(url, json={"version": active.version, "action": "abandon"}).status_code == 400
        assert client.post(url, json={"version": active.version, "action": "abandon", "confirm": True}).status_code == 200
        assert client.get(url).json() is None
        store.set_fallback_dir("scope", "Missing", active=True)
        store.set_fallback_dir("scope", "Keep", active=True)
        store.set_fallback_missing("scope", {"Missing": 3})
        store.begin_batch("scope", batch)
        latest = client.get(url).json()
        payload = {"version": latest["version"], "action": "abandon_directory", "directory": "Missing", "confirm": True}
        assert client.post(url, json={**payload, "directory": "Keep"}).status_code == 409
        assert client.post(url, json=payload).status_code == 200
        assert client.get(url).json()["fallback_dirs"] == ["Keep"]
        assert store.load().batch["task_id"] == "job"
        assert store.load().fallback_missing == {}
        assert client.post(url, json={**payload, "directory": "Keep"}).status_code == 409
    with Session(engine) as db:
        assert {row.action for row in db.query(AuditLog).all()} == {
            "task.drama.transfer_recovery.retry", "task.drama.transfer_recovery.abandon",
            "task.drama.transfer_recovery.abandon_directory",
        }
    engine.dispose()


@pytest.mark.parametrize("scenario", ["processing", "lost-response", "remote-failed", "rename-unverified",
                                     "query-error", "listing-unverified", "missing-output", "malformed-output"])
def test_drama_recovers_original_quark_batch(tmp_path, monkeypatch, scenario):
    engine = create_engine("sqlite:///" + (tmp_path / "tasks.db").as_posix())
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        task = Task(task_uid="test-quark", task_type="drama", taskname="show",
                    enabled=True, shareurl="https://pan.quark.cn/s/example", savepath="/show",
                    pattern=r"Episode (\d+)", replace=r"E\1", extra_json='{"allow_once": true}')
        db.add(task)
        db.commit()
        task_id = task.id

    submitted = []
    queried = []
    renames = []
    saved = {}
    remote_finished = False

    def save(fids, *args):
        submitted.append(fids)
        if scenario == "lost-response":
            raise TimeoutError("cloud response lost after submit")
        return {"code": 0, "status": 200, "data": {"task_id": "remote-1"}}

    def query(job):
        queried.append(job)
        with Session(engine) as db:
            assert db.get(DramaTransferCheckpoint, task_id).batch["task_id"] == job
        if scenario == "query-error" and not remote_finished:
            return {"code": 1, "status": 401, "message": "login required"}
        if scenario in ("missing-output", "malformed-output"):
            ids = "new-fid" if scenario == "malformed-output" else []
            return {"code": 0, "status": 200, "data": {
                "status": 2, "save_as": {"save_as_top_fids": ids},
            }}
        if scenario == "listing-unverified":
            assert not remote_finished, "A known output fid must still work after the task status expires"
        if not remote_finished and scenario not in ("rename-unverified", "listing-unverified"):
            return {"code": 0, "status": 200, "data": {"status": 3 if scenario == "remote-failed" else 1}}
        if not saved:
            saved["new-fid"] = "Episode 01.mkv"
        return {"code": 0, "status": 200, "data": {"status": 2,
                "save_as": {"save_as_top_fids": ["new-fid"]}}}

    def rename(fid, target):
        renames.append(fid)
        saved[fid] = target
        return {"code": 0, "status": 200}

    def listing(*args, **kwargs):
        if scenario == "listing-unverified" and saved and not remote_finished:
            raise ValueError("listing temporarily unavailable")
        if scenario == "rename-unverified" and renames and not remote_finished:
            raise ValueError("directory listing temporarily unavailable")
        return {"code": 0, "data": {"list": [
            {"fid": fid, "file_name": name} for fid, name in saved.items()]}}

    adapter = object.__new__(QuarkAdapter)
    adapter.is_active = True
    adapter.nickname = "account"
    adapter.savepath_fid = {}
    adapter.extract_url = lambda _: ("example", "", "", [])
    adapter.get_stoken = lambda *args: {"code": 0, "data": {"stoken": "token"}}
    adapter.get_fids = lambda paths: [{"file_path": paths[0], "fid": "dest"}]
    adapter.ls_dir = listing
    adapter.get_detail = lambda *args: {"code": 0, "data": {"list": [
        {"fid": "source-fid", "fid_token": "source-token", "file_name": "Episode 01.mkv", "size": 100}],
    }, "metadata": {"_total": 1}}
    adapter.save_file = save
    adapter.query_task = query
    adapter.rename = rename
    adapter.close = lambda: None

    class Manager:
        @classmethod
        def from_payload(cls, payload):
            return cls()

        def init_for_tasks(self, tasks):
            pass

        def get_adapter_for_task(self, task):
            return adapter

    monkeypatch.setattr(task_run_stages, "DatabaseAccountManager", Manager)
    command = RunTaskCommand(task_id)
    with Session(engine) as db:
        first = run_task_use_case(db).execute(command, RunPolicy.for_trigger("scheduler"))
        assert first.status == ("failed" if scenario in ("remote-failed", "query-error") else "skipped"), first.run_log
        if scenario == "query-error":
            assert "login required" in first.run_log
        assert db.get(Task, task_id).enabled
        checkpoint = db.get(DramaTransferCheckpoint, task_id)
        if scenario == "remote-failed":
            assert checkpoint is None
        elif scenario == "rename-unverified":
            assert checkpoint.renames_started
            assert checkpoint.batch is None
            assert checkpoint.confirmed[0]["saved_fids"] == ["new-fid"]
        else:
            assert checkpoint.batch["task_id"] == ("" if scenario == "lost-response" else "remote-1")
        if scenario == "listing-unverified":
            assert checkpoint.batch["output_fids"] == ["new-fid"]
        if scenario == "lost-response":
            assert checkpoint.recovery["blocked"]
    if scenario in ("missing-output", "malformed-output"):
        for _ in range(3):
            with Session(engine) as db:
                result = run_task_use_case(db).execute(command, RunPolicy.for_trigger("http"))
                assert result.status == "skipped"
        with Session(engine) as db:
            checkpoint = db.get(DramaTransferCheckpoint, task_id)
            assert not checkpoint.batch["output_fids"]
            assert checkpoint.recovery == {
                "attempts": 3, "blocked": True, "reason": "网盘未返回转存文件 ID，转存结果待确认",
            }
        assert queried == ["remote-1"] * 3
        assert submitted == [["source-fid"]]
        engine.dispose()
        return
    remote_finished = True
    with Session(engine) as db:
        resumed = run_task_use_case(db).execute(command, RunPolicy.for_trigger("http"))
        if scenario == "lost-response":
            assert resumed.status == "skipped"
            assert "未取得夸克任务 ID" in resumed.run_log
            assert db.get(Task, task_id).enabled
            assert submitted == [["source-fid"]]
            assert queried == []
            return
        assert resumed.status == "success", resumed.run_log
        assert resumed.adapter_snapshot["new_files"] == ["E01.mkv"]
        assert not db.get(Task, task_id).enabled
        assert db.get(DramaTransferCheckpoint, task_id) is None

    assert submitted == [["source-fid"]] * (2 if scenario == "remote-failed" else 1)
    assert queried == ["remote-1"] * (1 if scenario in ("rename-unverified", "listing-unverified") else 2)
    assert renames == ["new-fid"]
    assert saved == {"new-fid": "E01.mkv"}
    engine.dispose()


@pytest.mark.parametrize("pause_last_batch", [False, True])
def test_normal_drama_submits_all_batches_before_renaming(pause_last_batch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        task = Task(task_uid="multi-batch", task_type="drama", taskname="show", enabled=True,
                    shareurl="share", savepath="/show")
        db.add(task)
        db.commit()
        task_id = task.id

    names = ["prefix-prefix-B.mkv", *(f"other-extra-{i}.mkv" for i in range(99)), "prefix-B.mkv"]
    source = {str(i): name for i, name in enumerate(names)}
    destination = {}
    jobs = {}
    last_batch_ready = not pause_last_batch

    def save(fids, *args):
        for fid in fids:
            if source[fid] in destination.values():
                return {"code": 1, "message": "existing name"}
        job = str(len(jobs))
        jobs[job] = fids
        return {"code": 0, "data": {"task_id": job}}

    def query(job):
        if job == "1" and not last_batch_ready:
            return {"code": 0, "data": {"status": 1}}
        fids = []
        for fid in jobs[job]:
            destination["out-" + fid] = source[fid]
            fids.append("out-" + fid)
        return {"code": 0, "data": {"status": 2, "save_as": {"save_as_top_fids": fids}}}

    def rename(fid, target):
        if target in destination.values():
            return {"code": 1, "message": "existing name"}
        destination[fid] = target
        return {"code": 0}

    adapter = object.__new__(QuarkAdapter)
    adapter.savepath_fid = {}
    adapter.extract_url = lambda _: ("share", "", "0", [])
    adapter.get_stoken = lambda *args: {"code": 0, "data": {"stoken": "token"}}
    adapter.get_fids = lambda paths: [{"file_path": paths[0], "fid": "target-dir"}]
    adapter.get_detail = lambda *args: {"code": 0, "data": {"list": [
        {"fid": fid, "fid_token": "token", "file_name": name} for fid, name in source.items()
    ]}, "metadata": {"_total": len(source)}}
    adapter.ls_dir = lambda *args, **kwargs: {"code": 0, "data": {"list": [
        {"fid": fid, "file_name": name} for fid, name in destination.items()
    ]}}
    adapter.save_file = save
    adapter.query_task = query
    adapter.rename = rename
    task_data = {
        "task_uid": "multi-batch", "shareurl": "share", "savepath": "/show", "manual_run": True,
        "pattern": "^(prefix-|other-)", "replace": "",
        "drama_transfer_store": DramaTransferStore(lambda: Session(engine), task_id),
    }

    if pause_last_batch:
        with pytest.raises(SkipTask):
            DramaTaskExecutor(adapter=adapter, task_data=task_data).execute()
        assert destination["out-0"] == "prefix-prefix-B.mkv"
        last_batch_ready = True
        source["late"] = "other-prefix-B.mkv"
    DramaTaskExecutor(adapter=adapter, task_data=task_data).execute()

    assert [len(batch) for batch in jobs.values()] == [100, 1]
    assert destination["out-0"] == "prefix-B.mkv"
    assert destination["out-100"] == "B.mkv"
    engine.dispose()


def test_resuming_whole_folder_preserves_files_with_different_extensions():
    previous = {"fid": "already-saved", "file_name": "episode.mkv"}
    source = [
        {"fid": "old", "file_name": "episode.mkv"},
        {"fid": "new", "file_name": "episode.mp4"},
    ]
    destination = [previous]

    def save(fids, *args):
        assert fids == ["new"]
        destination.append({"fid": "new-saved", "file_name": "episode.mp4"})
        return {"code": 0, "data": {"_sync": True, "save_as_top_fids": ["new-saved"]}}

    adapter = SimpleNamespace(ls_dir=lambda *args, **kw: {"code": 0, "data": {"list": destination}},
                              get_detail=lambda *args: {"code": 0, "data": {"list": source}},
                              save_file=save)
    executor = DramaTaskExecutor(adapter=adapter, task_data={})
    tree = Tree()
    tree.create_node("show", "root")
    saved = executor._sync_share_dir_incremental(
        pwd_id="share", stoken="token", share_fid="folder", share_name="Folder",
        dest_base_path="/show", ignore_extension=True, tree=tree, rel_label="Folder",
        depth=0, copy_all=False, known_dest_fid="dest-folder", resume_copy=True,
    )

    assert saved == ["episode.mp4"]


@pytest.mark.parametrize("listing_fails", [False, True])
def test_rename_does_not_repeat_verified_or_unknown_writes(listing_fails):
    names = {"saved": "original"}
    writes = []
    lists = []

    def rename(fid, target):
        writes.append((fid, target))
        names[fid] = target
        return {"code": 0}

    def listing(*args, **kwargs):
        lists.append(args[0])
        if listing_fails:
            raise ValueError("listing unavailable")
        return {"code": 0, "data": {"list": [
            {"fid": fid, "file_name": name} for fid, name in names.items()]}}

    executor = DramaTaskExecutor(adapter=SimpleNamespace(rename=rename, ls_dir=listing), task_data={})
    executor._rename_by_fids(saved_fids=["saved"], plan=[DramaPlanItem("source", None, "original", "target")],
                             dest_root_fid="destination")

    assert writes == [("saved", "target")]
    assert lists == ["destination"]


def test_incremental_children_reuse_fids_and_mkdir_response():
    lookups = []
    created = []
    lists = []

    def listing(fid, **kwargs):
        lists.append(fid)
        items = [{"fid": "existing-dest", "file_name": "Existing", "dir": True}] if fid == "root-dest" else []
        return {"code": 0, "data": {"list": items}}

    def detail(pwd, token, fid):
        items = [
            {"fid": "existing-src", "file_name": "Existing", "dir": True},
            {"fid": "new-src", "file_name": "New", "dir": True},
        ] if fid == "share-root" else []
        return {"code": 0, "data": {"list": items}}

    def get_fids(paths):
        lookups.extend(paths)
        return [{"file_path": "/show", "fid": "root-dest"}] if paths == ["/show"] else []

    def mkdir(path):
        created.append(path)
        return {"code": 0, "data": {"fid": "new-dest"}}

    adapter = SimpleNamespace(savepath_fid={}, SUPPORTS_COMPLETE_LISTING=True,
        extract_url=lambda _: ("share", "", "share-root", []),
        get_stoken=lambda *args: {"code": 0, "data": {"stoken": "token"}},
        get_fids=get_fids, ls_dir=listing, ls_dir_complete=listing,
        get_detail=detail, mkdir=mkdir)
    DramaTaskExecutor(adapter=adapter, task_data={
        "savepath": "/show", "manual_run": True, "addition": {"folder_mode": "folder_diff"},
    }).execute()

    assert lookups == ["/show"]
    assert created == ["/show/New"]
    assert lists == ["root-dest", "existing-dest", "new-dest"]


def test_depth_limit_preserves_successful_sibling_and_keeps_once_enabled(monkeypatch):
    from app.infrastructure.execution.task_executor import TaskExecutor
    from app.domain.task_run import TaskRunState
    from app.core.settings import settings

    monkeypatch.setattr(settings, "drama_sync_max_depth", 4, raising=False)

    children = {
        str(depth): [{"fid": str(depth + 1), "file_name": str(depth + 1), "dir": True}]
        for depth in range(5)
    }
    children["0"].append({"fid": "shallow", "file_name": "Shallow", "dir": True})
    children["shallow"] = [{"fid": "episode", "file_name": "Show.S01E01.mkv", "size": 100}]
    writes = []

    def save(fids, *args):
        writes.extend(fids)
        return {"code": 0, "data": {"_sync": True, "save_as_top_fids": ["saved"]}}

    adapter = SimpleNamespace(is_active=True, savepath_fid={},
        extract_url=lambda _: ("share", "", "0", []),
        get_stoken=lambda *args: {"code": 0, "data": {"stoken": "token"}},
        get_fids=lambda paths: [{"file_path": paths[0], "fid": paths[0]}],
        ls_dir=lambda fid, **kw: {"code": 0, "data": {"list": [
            {"fid": "saved", "file_name": "Show.S01E01.mkv"}] if writes else []}},
        get_detail=lambda pwd, token, fid: {"code": 0, "data": {"list": children.get(fid, [])}},
        save_file=save)
    task = TaskRunState(id=1, task_uid="deep", taskname="show", task_type="drama", shareurl="share",
                        savepath="/show", addition_json='{"folder_mode":"folder_diff"}',
                        extra_json='{"allow_once":true}')
    executor = TaskExecutor(None)
    result = executor.run_task(task, persist_execution=False, init_account_for_task=False,
                              prepared_data=executor._task_to_dict(task),
                              account_manager=SimpleNamespace(get_adapter_for_task=lambda _: adapter))

    assert result.status == "failed", result.run_log
    assert "1/2/3/4/5" in result.run_log
    assert "Show.S01E01.mkv" in result.tree_summary
    assert writes == ["episode"]
    assert task.enabled


def _store_for_drama(engine):
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        task = Task(task_uid="checkpoint", task_type="drama", taskname="show", enabled=True,
                    shareurl="share", savepath="/show")
        db.add(task)
        db.commit()
        task_id = task.id
    return DramaTransferStore(lambda: Session(engine), task_id)


def test_renaming_without_confirmed_batches_requires_manual_confirmation():
    engine = create_engine("sqlite://")
    store = _store_for_drama(engine)
    executor = DramaTaskExecutor(adapter=SimpleNamespace(), task_data={"drama_transfer_store": store})
    tree = Tree()
    assert not executor._resume_transfer(tree)
    store.start_renames(executor._transfer_scope)

    with pytest.raises(SkipTask, match="没有已确认的转存批次"):
        executor._resume_transfer(tree)
    assert store.load().recovery["blocked"]
    engine.dispose()


@pytest.mark.parametrize("range_change", [None, "whitelist", "exclude", "partial"])
def test_missing_fallback_resumes_when_directory_reappears(range_change, monkeypatch):
    from app.core.settings import settings
    from app.infrastructure.persistence.repositories.drama_transfers import describe_checkpoint

    limit = 2 if range_change == "exclude" else 3
    monkeypatch.setattr(settings, "drama_fallback_missing_limit", limit)
    engine = create_engine("sqlite://")
    store = _store_for_drama(engine)
    reads = []
    writes = []
    returned = False
    subtree_complete = range_change != "partial"
    total = 3

    def detail(pwd, token, fid):
        reads.append(fid)
        rows = [
            {"fid": "live-src", "file_name": "Live", "dir": True},
            {"fid": "missing-src" if returned else "file-src", "file_name": "Missing", "dir": returned},
        ] if fid == "0" else []
        if fid == "missing-src":
            rows = [{"fid": "episode-src", "file_name": "Episode02.mp4", "dir": False}]
        expected = total if fid == "0" else len(rows)
        if fid == "missing-src" and not subtree_complete:
            expected += 1
        return {"code": 0, "data": {"list": rows}, "metadata": {"_total": expected}}

    def listing(fid, **kwargs):
        rows = [
            {"fid": "live-dest", "file_name": "Live", "dir": True},
            {"fid": "missing-dest" if returned else "file-dest", "file_name": "Missing", "dir": returned},
        ] if fid == "root" else []
        if fid == "missing-dest":
            rows = [{"fid": "already-saved", "file_name": "Episode02.mkv", "dir": False}]
            if writes:
                rows.append({"fid": "episode-dest", "file_name": "Episode02.mp4", "dir": False})
        return {"code": 0, "data": {"list": rows}}

    def save_files(fids, tokens, destination, *args):
        writes.append((fids, destination))
        assert store.load().fallback_missing == {}
        return {"code": 0, "data": {"_sync": True, "save_as_top_fids": ["episode-dest"]}}

    adapter = SimpleNamespace(savepath_fid={}, extract_url=lambda _: ("share", "", "0", []),
                              get_stoken=lambda *args: {"code": 0, "data": {"stoken": "token"}},
                              get_fids=lambda paths: [{"file_path": paths[0], "fid": "root"}],
                              ls_dir=listing, get_detail=detail, save_file=save_files)
    data = {"task_uid": "checkpoint", "shareurl": "share", "savepath": "/show", "manual_run": True,
            "ignore_extension": True,
            "addition": {"folder_mode": "folder_diff", "whitelist_folder_names": ["Live"]},
            "drama_transfer_store": store}
    executor = DramaTaskExecutor(adapter=adapter, task_data=data)
    assert not executor._resume_transfer(Tree())
    store.set_fallback_dir(executor._transfer_scope, "Missing", active=True)
    with Session(engine) as db:
        assert describe_checkpoint(db, db.query(Task.id).scalar())["reason"] == "有目录等待继续同步"
    executor.execute()
    assert store.load().fallback_missing == {}
    assert executor.incomplete_directories == ["/", "Missing"]

    total = 2
    for attempts in [*range(1, limit + 1), limit]:
        executor = DramaTaskExecutor(adapter=adapter, task_data=data)
        executor.execute()
        assert store.load().fallback_missing == {"Missing": attempts}
        assert executor.deferred_directories == (["Missing"] if attempts == limit else [])
        assert executor.incomplete_directories == ([] if attempts == limit else ["Missing"])
    assert reads.count("live-src") == limit + 2
    assert not store.load().recovery.get("blocked")
    with Session(engine) as db:
        assert describe_checkpoint(db, db.query(Task.id).scalar())["paused_fallback_dirs"] == ["Missing"]

    returned = True
    selected = {**data, "addition": {**data["addition"], "whitelist_folder_names": ["Live", "Missing"]}}
    if range_change == "exclude":
        excluded = {**data, "addition": {**data["addition"], "folder_exclude": "Missing"}}
        blocked_dir = DramaTaskExecutor(adapter=adapter, task_data=excluded)
        blocked_dir.execute()
        assert blocked_dir.deferred_directories == ["Missing"]
        assert blocked_dir.incomplete_directories == []
        assert store.load().fallback_dirs == ["Missing"]
        assert not writes
        with Session(engine) as db:
            view = describe_checkpoint(db, db.query(Task.id).scalar())
        assert view["excluded_fallback_dirs"] == ["Missing"]
        assert view["reason"] == "部分目录按当前规则排除"
    if range_change == "partial":
        partial = DramaTaskExecutor(adapter=adapter, task_data=selected)
        partial.execute()
        assert partial.incomplete_directories == ["Missing"]
        assert store.load().fallback_dirs == ["Missing"]
        assert not writes
        subtree_complete = True
    executor = DramaTaskExecutor(adapter=adapter, task_data=selected if range_change else data)
    executor.execute()
    assert writes == [(["episode-src"], "missing-dest")]
    assert executor.deferred_directories == executor.incomplete_directories == []
    assert store.load() is None
    engine.dispose()


def test_range_change_does_not_resume_a_batch_with_unconfirmed_writes():
    from app.infrastructure.persistence.repositories.drama_transfers import TransferBatch

    engine = create_engine("sqlite://")
    store = _store_for_drama(engine)
    data = {"savepath": "/show", "addition": {"folder_mode": "folder_diff", "whitelist_folder_names": ["Live"]},
            "drama_transfer_store": store}
    original = DramaTaskExecutor(adapter=SimpleNamespace(), task_data=data)
    assert not original._resume_transfer(Tree())
    store.set_fallback_dir(original._transfer_scope, "Missing", active=True)
    assert not original._resume_transfer(Tree())
    store.begin_batch(original._transfer_scope, TransferBatch("dest", [("fid", "episode", "episode")], task_id="pending"))
    changed = DramaTaskExecutor(adapter=SimpleNamespace(), task_data={
        **data, "addition": {**data["addition"], "whitelist_folder_names": ["Live", "Missing"]},
    })

    with pytest.raises(SkipTask, match="任务配置已变更"):
        changed._resume_transfer(Tree())
    assert store.load().batch["task_id"] == "pending"
    assert store.load().scope == original._transfer_scope
    engine.dispose()


@pytest.mark.parametrize("legacy", [False, True])
def test_configuration_block_clears_when_original_configuration_returns(legacy):
    from app.infrastructure.persistence.repositories.drama_transfers import describe_checkpoint

    engine = create_engine("sqlite://")
    store = _store_for_drama(engine)
    data = {"savepath": "/show", "drama_transfer_store": store}
    executor = DramaTaskExecutor(adapter=SimpleNamespace(), task_data=data)
    assert not executor._resume_transfer(Tree())
    scope = executor._transfer_scope
    store.set_fallback_dir(scope, "Original", active=True)
    if legacy:
        store.record_problem(scope, "任务配置已变更，已有转存待确认", manual=True)
    changed = DramaTaskExecutor(adapter=SimpleNamespace(), task_data={**data, "savepath": "/another"})
    with pytest.raises(SkipTask, match="任务配置已变更"):
        changed._resume_transfer(Tree())
    blocked = store.load()
    with Session(engine) as db:
        task_id = db.query(Task.id).scalar()
        view = describe_checkpoint(db, task_id)
    assert view["requires_confirmation"]
    assert not view["can_retry"]

    with pytest.raises(SkipTask, match="任务配置已变更"):
        changed._resume_transfer(Tree())
    assert store.load().version == blocked.version

    restored = DramaTaskExecutor(adapter=SimpleNamespace(), task_data=data)
    assert not restored._resume_transfer(Tree())
    assert restored._fallback_dirs == {"Original"}
    assert store.load().recovery == {}
    engine.dispose()


@pytest.mark.parametrize("nested", [False, True])
def test_same_named_destinations_are_never_selected(nested):
    lookups, reads = [], []
    duplicate_dirs = [{"fid": fid, "file_name": "Duplicate", "dir": True} for fid in ("a", "b")]

    def get_fids(paths):
        lookups.extend(paths)
        return [{"file_path": paths[0], "fid": "root"}]

    def listing(fid, **kwargs):
        items = [{"fid": "live-dest", "file_name": "Live", "dir": True}] if nested and fid == "root" else duplicate_dirs
        return {"code": 0, "data": {"list": items}}

    def detail(pwd, token, fid):
        reads.append(fid)
        item = {"fid": "live-src", "file_name": "Live", "dir": True} if nested and fid == "0" else {
            "fid": "duplicate-src", "file_name": "Duplicate", "dir": True,
        }
        return {"code": 0, "data": {"list": [item] if fid != "duplicate-src" else []}}

    adapter = SimpleNamespace(savepath_fid={}, extract_url=lambda _: ("share", "", "0", []),
                              get_stoken=lambda *args: {"code": 0, "data": {"stoken": "token"}},
                              get_fids=get_fids, ls_dir=listing, get_detail=detail)
    executor = DramaTaskExecutor(adapter=adapter, task_data={
        "savepath": "/show", "manual_run": True, "addition": {"folder_mode": "folder_diff"},
    })

    executor.execute()

    assert lookups == ["/show"]
    assert reads == (["0", "live-src"] if nested else ["0"])
    assert executor.incomplete_directories == (["Live/Duplicate"] if nested else ["Duplicate"])


def test_explicit_path_mismatch_does_not_select_another_directory():
    adapter = SimpleNamespace(get_fids=lambda _: [{"file_path": "/another-show", "fid": "wrong"}])
    executor = DramaTaskExecutor(adapter=adapter, task_data={})

    with pytest.raises(RuntimeError, match="目标目录路径无法核实"):
        executor._try_get_dir_fid("/show")


@pytest.mark.parametrize("scope", ["normal", "incremental-root", "incremental-child", "empty", "read-error"])
def test_quark_share_pagination_controls_once_completion(scope, monkeypatch):
    from app.core.settings import settings
    from app.domain.task_run import TaskRunState
    from app.infrastructure.execution.task_executor import TaskExecutor

    monkeypatch.setattr(settings, "drama_runtime_retry_max_attempts", 1)
    episodes = [
        {"fid": "source-1", "file_name": "Series.S01E01.mkv", "share_fid_token": "file-token-1"},
        {"fid": "source-2", "file_name": "Series.S01E02.mkv", "share_fid_token": "file-token-2"},
    ]
    child = scope == "incremental-child"
    share_dir, dest_dir = ("season-src", "season-dest") if child else ("0", "root-dest")
    destination = {
        "root-dest": [{"fid": "season-dest", "file_name": "Season", "dir": True}] if child else [],
        "season-dest": [],
    }
    if scope != "empty":
        destination[dest_dir].append({"fid": "existing", "file_name": episodes[0]["file_name"]})
    share_reads, target_reads, writes = [], [], []
    repaired = False

    def send(method, url, *, params):
        assert method == "GET" and url.endswith("/sharepage/detail")
        fid, page = params["pdir_fid"], params["_page"]
        share_reads.append((fid, page))
        if child and fid == "0":
            assert page == 1
            items, total = [{"fid": "season-src", "file_name": "Season", "dir": True}], 1
        elif scope == "empty":
            assert fid == share_dir and page == 1
            items, total = [], 0
        else:
            assert fid == share_dir and page in (1, 2)
            if scope == "read-error" and page == 2 and not repaired:
                return SimpleNamespace(json=lambda: {"code": 1, "message": "share read failed"})
            items = episodes[page - 1:page] if page == 1 or repaired else []
            total = 2
        return SimpleNamespace(json=lambda: {
            "code": 0, "status": 200, "data": {"list": items}, "metadata": {"_total": total},
        })

    def save(fids, tokens, target, share, stoken):
        writes.append((tuple(fids), tuple(tokens), target))
        assert fids == ["source-2"]
        destination[target].append({"fid": "saved-2", "file_name": episodes[1]["file_name"]})
        return {"code": 0, "data": {"_sync": True, "save_as_top_fids": ["saved-2"]}}

    def listing(fid, **kwargs):
        target_reads.append(fid)
        return {"code": 0, "data": {"list": destination[fid]}}

    adapter = object.__new__(QuarkAdapter)
    adapter.is_active = True
    adapter.savepath_fid = {}
    adapter.extract_url = lambda _: ("share", "", "0", [])
    adapter.get_stoken = lambda *args: {"code": 0, "data": {"stoken": "token"}}
    adapter.get_fids = lambda paths: [{"file_path": paths[0], "fid": "root-dest"}]
    adapter.ls_dir = listing
    adapter.ls_dir_complete = listing
    adapter.save_file = save
    monkeypatch.setattr(adapter, "_send_request", send)

    addition = '{"folder_mode":"folder_diff"}' if scope.startswith("incremental") else "{}"
    task = TaskRunState(id=1, task_uid="paged", taskname="Series", task_type="drama", shareurl="share",
                        savepath="/show", addition_json=addition, extra_json='{"allow_once":true}')
    executor = TaskExecutor(None)

    def run():
        return executor.run_task(task, persist_execution=False, init_account_for_task=False, manual_run=True,
                                 prepared_data=executor._task_to_dict(task),
                                 account_manager=SimpleNamespace(get_adapter_for_task=lambda _: adapter))

    first = run()
    assert writes == []
    assert target_reads == ["root-dest"]
    if scope == "empty":
        assert share_reads == [("0", 1)]
        assert first.status == "success"
        assert "无可转存文件" in first.tree_summary
        assert task.enabled
        return

    expected_pages = ([("0", 1)] if child else []) + [(share_dir, 1), (share_dir, 2)]
    assert share_reads == expected_pages
    assert first.status == "failed", first.run_log
    assert ("share read failed" if scope == "read-error" else "分享目录列表不完整") in first.run_log
    assert "无可转存文件" not in (first.tree_summary or "")
    assert task.enabled

    repaired = True
    second = run()
    assert share_reads == expected_pages * 2
    expected_targets = ["root-dest", "root-dest", dest_dir, dest_dir] if child else ["root-dest"] * 3
    assert target_reads == expected_targets
    assert writes == [(("source-2",), ("file-token-2",), dest_dir)]
    assert second.status == "success", second.run_log
    assert "Series.S01E02.mkv" in second.tree_summary
    assert not task.enabled


@pytest.mark.parametrize("first_page", [False, True])
def test_quark_partial_listing_preserves_explicit_first_page_contract(monkeypatch, first_page):
    adapter = object.__new__(QuarkAdapter)
    reads = []
    entry = {"fid": "source-1", "file_name": "Series.S01E01.mkv"}
    initial = {"code": 0, "data": {"list": [entry]}, "metadata": {"_total": 2}}

    def send(method, url, *, params):
        page = params["_page"]
        reads.append(page)
        response = initial if page == 1 else {
            "code": 0, "data": {"list": []}, "metadata": {"_total": 2},
        }
        return SimpleNamespace(json=lambda: response)

    monkeypatch.setattr(adapter, "_send_request", send)

    result = adapter.get_detail("share", "token", "0", first_page=first_page)

    if first_page:
        assert reads == [1]
        assert result is initial
        assert result["metadata"] == {"_total": 2}
    else:
        assert reads == [1, 2]
        assert result["data"]["list"] == [entry]
        assert result["metadata"]["_total"] == 2
        assert result["metadata"]["_listing_complete"] is False
