import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.application.container import run_task_use_case
from app.application.use_cases.run_task import RunPolicy
from app.core.settings import settings
from app.domain.task_run import RunTaskCommand
from app.infrastructure.execution import task_run_stages
from app.infrastructure.drive.adapters.baidu_adapter import BaiduAdapter
from app.infrastructure.execution.drama_executor import DramaTaskExecutor
from app.infrastructure.persistence.repositories.drama_transfers import DramaTransferStore
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.task import Task


@pytest.mark.parametrize("outcome, status", [("rejected", "failed"), ("timeout", "skipped"), ("confirmed", "success")])
def test_normal_rename_result_and_recovery(monkeypatch, outcome, status):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        task = Task(task_uid="normal", task_type="drama", taskname="Show", enabled=True,
                    shareurl="https://example.test/s/id", savepath="/show", pattern="Original", replace="Series",
                    extra_json='{"allow_once":true}')
        db.add(task)
        db.commit()
        task_id = task.id
    monkeypatch.setattr(settings, "drama_runtime_retry_max_attempts", 1)
    origin, target = "Original.S01E03.mkv", "Series.S01E03.mkv"
    names, saves, writes, reads = {}, [], [], []

    def save(fids, *args):
        saves.append(fids)
        names["saved"] = origin
        return {"code": 0, "data": {"save_as_top_fids": ["saved"]}}

    def rename(fid, name):
        writes.append((fid, name))
        if outcome == "rejected":
            return {"code": 1, "message": "denied"}
        names[fid] = name
        return {"code": 0}

    def listing(fid, **kwargs):
        reads.append(fid)
        if writes and outcome == "timeout":
            raise TimeoutError()
        return {"code": 0, "data": {"list": [{"fid": k, "file_name": v} for k, v in names.items()]}}

    adapter = SimpleNamespace(is_active=True, nickname="test", DRIVE_TYPE="test",
        extract_url=lambda url: ("share", "", "", []),
        get_stoken=Mock(return_value={"data": {"stoken": "token"}}),
        get_fids=Mock(return_value=[{"file_path": "/show", "fid": "dest"}]),
        get_detail=Mock(return_value={"code": 0, "data": {"list": [{"file_name": origin, "fid": "src"}]}}),
        ls_dir=listing, save_file=save, rename=rename, close=lambda: None)
    manager = SimpleNamespace(init_for_tasks=lambda tasks: None, get_adapter_for_task=lambda task: adapter)
    monkeypatch.setattr(task_run_stages.DatabaseAccountManager, "from_payload", lambda *args: manager)

    def run():
        with Session(engine) as db:
            result = run_task_use_case(db).execute(RunTaskCommand(task_id), RunPolicy.for_trigger("http"))
            row = db.get(Task, task_id)
            return result, row.enabled, json.loads(row.extra_json)

    first, enabled, extra = run()
    assert first.status == status, first.run_log
    assert enabled == (status != "success")
    assert first.adapter_snapshot["transfer_count"] == 1
    assert (target in first.tree_summary) == (status == "success")
    assert (target in first.adapter_snapshot["new_files"]) == (status == "success")
    if outcome == "timeout":
        assert extra.get("saved_latest", {}).get("name") != target
    else:
        assert extra["saved_latest"]["name"] == (target if status == "success" else origin)
    assert saves == [["src"]]
    assert writes == [("saved", target)]
    assert reads == ["dest"] * 3
    if status != "success":
        expected_writes = 2 if outcome == "rejected" else 1
        outcome = "confirmed"
        recovered, enabled, extra = run()
        assert recovered.status == "success", recovered.run_log
        assert target in recovered.tree_summary and not enabled
        assert saves == [["src"]] and len(writes) == expected_writes
        assert reads == ["dest"] * (3 + expected_writes)
        assert extra["saved_latest"]["episode"] == 3
        assert extra["saved_latest"]["name"] == target
    assert [adapter.get_stoken.call_count, adapter.get_fids.call_count, adapter.get_detail.call_count] == [1, 1, 1]


def test_baidu_resume_verifies_stable_id_after_path_changes(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        task = Task(task_uid="baidu", task_type="drama", taskname="Show", shareurl="share", savepath="/show")
        db.add(task)
        db.commit()
        task_id = task.id
    origin, target = "Original.S01E01.mkv", "Series.S01E01.mkv"
    adapter = BaiduAdapter(cookie="BDUSS=fake")
    names, writes, saves = {}, [], []
    timeout = True

    def listing(parent, page):
        if writes and timeout:
            raise TimeoutError()
        return {"errno": 0, "list": [{"fs_id": fid, "path": f"/show/{name}", "isdir": 0}
                                      for fid, name in names.items()]}

    def rename(path, name):
        assert path == "/show/" + origin
        writes.append(path)
        names["123"] = name
        return {"code": 0}

    def save(fids, *args, **kwargs):
        saves.append(fids)
        names["123"] = origin
        return {"code": 0, "data": {"save_as_top_fids": ["/show/" + origin]}}

    monkeypatch.setattr(adapter, "_api_list_page", listing)
    monkeypatch.setattr(adapter, "extract_url", lambda url: ("share", "", "", []))
    monkeypatch.setattr(adapter, "get_stoken", lambda *args: {"data": {"stoken": "token"}})
    monkeypatch.setattr(adapter, "get_fids", lambda paths: [{"file_path": paths[0], "fid": "/show"}])
    monkeypatch.setattr(adapter, "get_detail", lambda *args: {"data": {"list": [{"file_name": origin, "fid": "source"}]}})
    monkeypatch.setattr(adapter, "_api_list", lambda parent: listing(parent, 1))
    monkeypatch.setattr(adapter, "rename", rename)
    monkeypatch.setattr(adapter, "save_file", save)
    monkeypatch.setattr(settings, "drama_runtime_retry_max_attempts", 1)

    def run():
        executor = DramaTaskExecutor(adapter=adapter, task_data={
            "shareurl": "share", "savepath": "/show", "taskname": "Show", "pattern": "Original", "replace": "Series",
            "drama_transfer_store": DramaTransferStore(lambda: Session(engine), task_id)})
        tree = executor.execute()
        return executor.rename_problem, str(tree)

    problem, _tree = run()
    assert problem[0] == "skipped"
    timeout = False
    names["456"] = origin  # Another file has occupied the old path.
    problem, tree = run()
    assert problem is None and target in tree
    assert writes == ["/show/" + origin] and saves == [["source"]]
