from unittest.mock import Mock

import pytest

from app.domain.drive_types import DriveFile, TransferRequest
from app.infrastructure.drive.adapters.baidu_adapter import BaiduAdapter
from app.infrastructure.drive.gateway import gateway_for_adapter


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr('app.infrastructure.drive.adapters.baidu_adapter.time.monotonic', lambda: elapsed[0])
    monkeypatch.setattr('app.infrastructure.drive.adapters.baidu_adapter.time.sleep', lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
    return elapsed


def item(name):
    return {'fid': f'/dest/{name}', 'file_name': name, 'dir': False}


def listing(names):
    return {'code': 0, 'data': {'list': [item(name) for name in names]}}


def setup_transfer(monkeypatch, source_names, saved_names, *, before=None, after=None):
    adapter = BaiduAdapter(cookie='BDUSS=fake')
    monkeypatch.setattr(adapter, '_resolve_fid_to_path', lambda fid: fid)
    monkeypatch.setattr(adapter, '_api_access_shared', lambda *args: {})
    monkeypatch.setattr(adapter, '_api_shared_paths', lambda *args: {'share_uk': 1, 'shareid': 2})
    write = Mock(return_value={'errno': 0})
    monkeypatch.setattr(adapter, '_api_transfer_shared_paths', write)
    snapshots = [before if before is not None else listing([]), after if after is not None else listing(saved_names)]
    # Both snapshot APIs share the same external fixture, so either implementation
    # exercises actual adapter parsing and the real transfer strategy.
    def read(*args, **kwargs):
        return snapshots.pop(0) if len(snapshots) > 1 else snapshots[0]
    monkeypatch.setattr(adapter, 'ls_dir', read)
    monkeypatch.setattr(adapter, 'ls_dir_complete', read)
    request = TransferRequest('share', 'share:1234', '/dest', tuple(
        DriveFile(str(i + 1), name, token=str(i + 1)) for i, name in enumerate(source_names)))
    return gateway_for_adapter(adapter), request, write


def delayed_listing(monkeypatch, gateway, snapshots):
    remaining = iter(snapshots)
    current = None
    def read(*args):
        nonlocal current
        current = next(remaining, current)
        if isinstance(current, Exception):
            raise current
        return current
    reader = Mock(side_effect=read)
    monkeypatch.setattr(gateway._adapter, 'ls_dir_complete', reader)
    return reader


def test_baidu_normalized_name_is_verified_and_associated(monkeypatch):
    gateway, request, write = setup_transfer(monkeypatch, ['第01集【4K】.mp4'], ['第01集4K.mp4'])
    result = gateway.transfer_files(request)
    assert result.state == 'completed' and result.confirmed
    assert [(file.source_id, file.id, file.name) for file in result.files] == [('1', '/dest/第01集4K.mp4', '第01集4K.mp4')]
    assert write.call_count == 1


def test_baidu_punctuation_collisions_remain_pending(monkeypatch):
    gateway, request, write = setup_transfer(monkeypatch, ['A().mp4'], ['A[].mp4', 'A{}.mp4'])
    result = gateway.transfer_files(request)
    assert result.state == 'pending' and not result.confirmed
    assert write.call_count == 1


def test_baidu_partial_ids_report_unconfirmed_files(monkeypatch):
    gateway, request, write = setup_transfer(monkeypatch, ['A.mp4', 'B.mp4'], ['A.mp4'])
    result = gateway.transfer_files(request)
    assert result.state == 'pending' and not result.confirmed
    assert 'B.mp4' in result.message
    assert 'A.mp4' not in result.message
    assert write.call_count == 1


def test_baidu_cannot_reuse_one_saved_file_for_two_sources(monkeypatch):
    gateway, request, write = setup_transfer(monkeypatch, ['A[].mp4', 'A{}.mp4'], ['A.mp4'])
    result = gateway.transfer_files(request)
    assert result.state == 'pending' and not result.confirmed
    assert write.call_count == 1


def test_baidu_failed_prewrite_snapshot_prevents_submission(monkeypatch):
    gateway, request, write = setup_transfer(monkeypatch, ['A.mp4'], ['A.mp4'], before={'code': 1, 'data': {'list': []}})
    result = gateway.transfer_files(request)
    assert result.state == 'failed' and not result.confirmed
    write.assert_not_called()


def test_baidu_failed_postwrite_snapshot_does_not_replay(monkeypatch):
    gateway, request, write = setup_transfer(monkeypatch, ['A.mp4'], ['A.mp4'], after={'code': 1, 'data': {'list': []}})
    result = gateway.transfer_files(request)
    assert result.state == 'pending' and not result.confirmed
    assert write.call_count == 1


def test_baidu_preexisting_same_name_is_not_attributed_to_new_write(monkeypatch):
    gateway, request, write = setup_transfer(monkeypatch, ['A.mp4'], ['A.mp4'], before=listing(['A.mp4']))
    result = gateway.transfer_files(request)
    assert result.state == 'pending' and not result.confirmed
    assert write.call_count == 1


def test_baidu_preexisting_file_on_later_page_requires_complete_snapshot(monkeypatch):
    gateway, request, write = setup_transfer(monkeypatch, ['A.mp4'], ['A.mp4'], before=listing(['A.mp4']))
    pages = [listing([]), listing(['A.mp4'])]
    monkeypatch.setattr(gateway._adapter, 'ls_dir', lambda *args: pages.pop(0) if len(pages) > 1 else pages[0])
    result = gateway.transfer_files(request)
    assert result.state == 'pending' and not result.confirmed
    assert write.call_count == 1


def test_baidu_batch_becomes_visible_after_empty_partial_and_failed_reads(monkeypatch, clock):
    names = ['2026.08.06-特辑4-4K.高码率.mp4', '2026.08.01-特辑3-4K.高码率.mp4',
             '2026.07.30-特辑2-4K.高码率.mp4'] + [f'episode-{i}.mp4' for i in range(78)]
    gateway, request, write = setup_transfer(monkeypatch, names, names)
    write.return_value = {'errno': 0, 'taskid': 999}
    reader = delayed_listing(monkeypatch, gateway, [listing([]), listing([]), listing(names[:25]),
                                                   RuntimeError('temporary read failure'), listing(list(reversed(names)))])
    result = gateway.transfer_files(request)
    assert result.state == 'completed' and result.confirmed
    assert [(file.source_id, file.id) for file in result.files] == [(str(i + 1), f'/dest/{name}') for i, name in enumerate(names)]
    assert result.task_id == '999'
    assert clock[0] > 5
    assert reader.call_count >= 5
    assert write.call_count == 1


def test_baidu_confirmation_does_not_depend_on_first_page_browse(monkeypatch):
    gateway, request, write = setup_transfer(monkeypatch, ['A.mp4'], ['A.mp4'])
    monkeypatch.setattr(gateway._adapter, 'ls_dir', lambda *args: listing([]))
    result = gateway.transfer_files(request)
    assert result.state == 'completed' and result.confirmed
    assert write.call_count == 1


def test_baidu_confirmation_timeout_keeps_pending_and_never_resubmits(monkeypatch, clock):
    gateway, request, write = setup_transfer(monkeypatch, ['A.mp4', 'B.mp4'], ['A.mp4'])
    reader = delayed_listing(monkeypatch, gateway, [listing([]), listing(['A.mp4'])])
    result = gateway.transfer_files(request)
    assert result.state == 'pending' and not result.confirmed
    assert 'B.mp4' in result.message and 'A.mp4' not in result.message
    assert 5 < clock[0] <= 60
    assert 2 < reader.call_count <= 15
    assert write.call_count == 1


def test_baidu_partial_snapshot_is_not_accumulated_as_full_success(monkeypatch):
    gateway, request, write = setup_transfer(monkeypatch, ['A.mp4', 'B.mp4'], ['B.mp4'])
    delayed_listing(monkeypatch, gateway, [listing([]), listing(['A.mp4']), listing(['B.mp4'])])
    result = gateway.transfer_files(request)
    assert result.state == 'pending' and not result.confirmed
    assert write.call_count == 1


def test_baidu_explicit_write_failure_is_not_polled_as_success(monkeypatch):
    gateway, request, write = setup_transfer(monkeypatch, ['A.mp4'], ['A.mp4'])
    write.return_value = {'errno': 12}
    reader = delayed_listing(monkeypatch, gateway, [listing([])])
    result = gateway.transfer_files(request)
    assert result.state == 'failed' and not result.confirmed
    assert reader.call_count == 1
    assert write.call_count == 1


def test_drama_pending_transfer_reports_save_stage(monkeypatch):
    from app.infrastructure.execution.drama_executor import DramaTaskExecutor, SkipTask
    from app.infrastructure.execution.drama_plan import DramaPlanItem
    from app.domain.drive_types import TransferResult
    log = Mock()
    executor = DramaTaskExecutor(adapter=Mock(), task_data={}, log=log)
    monkeypatch.setattr('app.infrastructure.execution.drama_executor.gateway_for_adapter', lambda *args, **kwargs:
                        Mock(transfer_files=Mock(return_value=TransferResult('pending', message='waiting'))))
    with pytest.raises(SkipTask):
        executor._save_with_saved_fids(pwd_id='share', stoken='token', dest_root_fid='/dest',
                                       plan=[DramaPlanItem('1', '1', 'A.mp4', 'A.mp4')])
    assert log.set_stage.call_args.args == ('save_file',)
