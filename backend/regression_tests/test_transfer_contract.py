import pytest
from types import SimpleNamespace
from app.infrastructure.drive.gateway import gateway_for_adapter
from app.infrastructure.drive.factory import AdapterFactory


def test_gateway_exposes_typed_transfer():
    assert callable(getattr(gateway_for_adapter(SimpleNamespace(DRIVE_TYPE="fake")), "transfer_files", None))

@pytest.mark.parametrize("drive", AdapterFactory.ADAPTER_MAP)
def test_registered_adapter_declares_transfer_strategy(drive):
    cls = AdapterFactory.ADAPTER_MAP[drive]
    assert getattr(cls, "TRANSFER_STRATEGY", None) is not None


def request():
    from app.domain.drive_types import DriveFile, TransferRequest
    return TransferRequest("share", "st", "dest", (DriveFile("a", "A", token="t"), DriveFile("b", "B", token="t")))


def fake(drive, response=None):
    from app.infrastructure.drive.transfer import TransferStrategy
    cls = AdapterFactory.ADAPTER_MAP.get(drive)
    strategy = cls.TRANSFER_STRATEGY if cls else TransferStrategy()
    events = []
    def save(*args, **kwargs):
        events.append(("save", args, kwargs))
        return response or {"code": 0, "data": {"_sync": True, "save_as_top_fids": ["2", "1"]}}
    return SimpleNamespace(DRIVE_TYPE=drive, TRANSFER_STRATEGY=strategy, events=events,
        save_file=save, classify_share_failure=lambda r: {"kind": r.get("kind", "unknown")},
        ls_dir=lambda p: {"code": 0, "data": {"list": [{"fid": "1", "file_name": "A"}, {"fid": "2", "file_name": "B"}]}},
        query_task=lambda j: {"code": 0, "data": {"status": 2, "save_as": {"save_as_top_fids": ["2", "1"], "save_as_select_top_fids": ["2", "1"]}}},
        get_or_create_share_folder=lambda: "stage", move_files_to_target=lambda ids, dest: {"code": 0})

@pytest.mark.parametrize("drive", AdapterFactory.ADAPTER_MAP)
@pytest.mark.parametrize("async_job", [False, True])
def test_all_drives_confirm_and_associate_by_name_not_id_order(drive, async_job):
    adapter = fake(drive, {"code": 0, "data": {"taskId": "job"}} if async_job else None)
    result = gateway_for_adapter(adapter).transfer_files(request())
    assert result.state == "completed" and result.confirmed
    assert [(x.source_id, x.id) for x in result.files] == [("a", "1"), ("b", "2")]
    assert len(adapter.events) == 1
    if drive == "uc":
        assert adapter.events[0][1][2] == "stage"

@pytest.mark.parametrize("kind", ["pending", "auth", "throttled"])
def test_failure_kinds_preserved(kind):
    result = gateway_for_adapter(fake("baidu", {"code": -7, "kind": kind})).transfer_files(request())
    assert result.kind == kind
    assert result.state == ("pending" if kind == "pending" else "failed")


def test_write_type_error_is_never_replayed():
    adapter = fake("baidu")
    def broken(*a, **k):
        adapter.events.append("write")
        raise TypeError("after remote commit")
    adapter.save_file = broken
    result = gateway_for_adapter(adapter).transfer_files(request())
    assert result.state == "pending" and not result.confirmed
    assert adapter.events == ["write"]


def test_uc_move_failure_is_not_completed():
    adapter = fake("uc")
    adapter.move_files_to_target = lambda *a: {"code": 1}
    assert gateway_for_adapter(adapter).transfer_files(request()).state == "failed"


def test_token_optional_for_non_token_drive():
    from dataclasses import replace
    req = request()
    req = replace(req, files=tuple(replace(f, token=None) for f in req.files))
    assert gateway_for_adapter(fake("baidu")).transfer_files(req).state == "completed"


def test_unconfirmed_submission_is_pending():
    adapter = fake("baidu", {"code": 0, "data": {"task_id": "job"}})
    adapter.query_task = lambda j: {"code": 0, "data": {"status": 1}}
    assert gateway_for_adapter(adapter).transfer_files(request()).state == "pending"

def test_sync_result_with_synthetic_job_does_not_poll():
    adapter = fake('baidu')
    adapter.save_file = lambda *a, **kw: {'code': 0, 'data': {'_sync': True, 'task_id': 'synthetic', 'save_as_top_fids': ['1', '2']}}
    adapter.query_task = lambda job: pytest.fail('synchronous response must not be polled')
    assert gateway_for_adapter(adapter).transfer_files(request()).state == 'completed'


def test_duplicate_source_names_are_not_associated():
    from dataclasses import replace
    req = request()
    req = replace(req, files=(req.files[0], replace(req.files[1], name='A')))
    result = gateway_for_adapter(fake('baidu')).transfer_files(req)
    assert result.state == 'pending' and not result.confirmed

def test_adapter_reported_uncertain_write_is_pending_and_checked():
    adapter = fake('baidu', {'code': 1, 'write_uncertain': True})
    checks = []
    adapter.ls_dir = lambda path: checks.append(path) or {'code': 0, 'data': {'list': []}}
    result = gateway_for_adapter(adapter).transfer_files(request())
    assert result.state == 'pending' and not result.confirmed
    assert len(adapter.events) == 1 and checks == ['dest']

def test_uc_uncertain_move_is_pending():
    adapter = fake('uc')
    adapter.move_files_to_target = lambda *args: {'code': 1, 'write_uncertain': True}
    result = gateway_for_adapter(adapter).transfer_files(request())
    assert result.state == 'pending' and not result.confirmed
