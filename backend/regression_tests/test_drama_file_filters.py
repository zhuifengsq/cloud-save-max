from unittest.mock import Mock

import pytest

from app.infrastructure.execution.drama_executor import DramaTaskExecutor
from app.infrastructure.execution.execution_log import ExecutionLog


@pytest.mark.parametrize("location", ["normal", "root", "incremental", "whole_folder"])
@pytest.mark.parametrize(("rules", "log_message"), [
    ({"filter_words": "群"}, "关键词过滤：移除 1 个匹配的文件"),
    ({"file_filter": "后会友期|mp4"}, "文件筛选：移除 1 个不匹配的文件"),
    ({"min_size": "1KB"}, "大小过滤：移除 1 个低于 1KB 的文件"),
    ({"file_min_date": "2026-01-01"}, "时间过滤：移除 1 个早于 2026-01-01 的文件"),
])
def test_drama_applies_file_filters_before_saving(location, rules, log_message):
    files = [
        {"fid": "ad", "fid_token": "ad-token", "file_name": "加群，获取更多资源.jpg", "size": 1, "updated_at": "2024-01-02"},
        {"fid": "video", "fid_token": "video-token", "file_name": "后会友期 S01E01.mp4", "size": 1024, "updated_at": "2026-01-01"},
    ]
    folder = {"fid": "extras", "fid_token": "folder-token", "file_name": "资源", "dir": True}
    share = {"selected": files if location in {"normal", "root"} else [folder], "extras": files}
    adapter = Mock()
    adapter.extract_url.return_value = ("share", "", "selected", [])
    adapter.get_stoken.return_value = {"data": {"stoken": "token"}}
    log = ExecutionLog()
    executor = DramaTaskExecutor(
        adapter=adapter,
        task_data={
            "taskname": "后会友期",
            "savepath": "/综艺/后会友期",
            "addition": {
                "folder_mode": "normal" if location == "normal" else "folder_diff",
                "whitelist_folders": "4k" if location == "whole_folder" else "",
                **rules,
            },
        },
        log=log,
    )
    executor._ensure_dest_dir_fid = Mock(side_effect=lambda path: f"dest:{path}")
    executor._ls_dir = Mock(return_value={"data": {"list": []}})
    adapter.get_detail.side_effect = lambda _pwd, _token, fid: {"data": {"list": share[fid]}}
    executor._save_with_saved_fids = Mock(return_value=["saved-video"])

    executor.execute()

    executor._save_with_saved_fids.assert_called_once()
    plan = executor._save_with_saved_fids.call_args.kwargs["plan"]
    assert [item.fid for item in plan] == ["video"]
    assert log_message in log.render()


@pytest.mark.parametrize('mode', ['normal', 'folder_diff'])
@pytest.mark.parametrize('has_source_files', [True, False])
def test_existing_destination_progress_is_recorded_without_new_transfers(mode, has_source_files):
    existing = [{'fid': 'saved-10', 'file_name': '测试剧 S01E10.mp4', 'size': 1024}]
    source = [{'fid': 'source-10', 'file_name': '测试剧 S01E10.mp4', 'size': 1024}] if has_source_files else []
    adapter = Mock()
    adapter.extract_url.return_value = ('share', '', 'selected', [])
    adapter.get_stoken.return_value = {'data': {'stoken': 'token'}}
    executor = DramaTaskExecutor(adapter=adapter, task_data={
        'taskname': '测试剧', 'savepath': '/测试剧', 'addition': {'folder_mode': mode},
    })
    executor._ensure_dest_dir_fid = Mock(return_value='dest')
    executor._ls_dir = Mock(return_value={'data': {'list': existing}})
    adapter.get_detail.return_value = {"data": {"list": source}}
    executor._save_with_saved_fids = Mock(side_effect=AssertionError('No files should be transferred'))
    executor.execute()
    executor._save_with_saved_fids.assert_not_called()
    assert executor.saved_latest is not None
    assert executor.saved_latest['episode'] == 10
    assert executor.saved_latest['name'] == '测试剧 S01E10.mp4'
    executor._ls_dir.assert_called_once_with('dest')


@pytest.mark.parametrize("ignore_extension", [False, True])
@pytest.mark.parametrize(("existing_name", "expected"), [
    ("Series.S01E05.srt", ["source"]),
    ("Series.S01E05.nfo", ["source"]),
    ("Series.S01E05.mp4", []),
])
def test_normal_episode_dedup_ignores_sidecars(ignore_extension, existing_name, expected):
    executor = DramaTaskExecutor(adapter=Mock(), task_data={"ignore_extension": ignore_extension})
    plan = executor._plan_transfer(
        share_files=[{"fid": "source", "file_name": "Series.S01E05.mkv"}],
        dest_file_list=[{"fid": "existing", "file_name": existing_name}],
    )
    assert [item.fid for item in plan] == expected


def test_empty_destination_does_not_claim_source_episodes_are_saved():
    adapter = Mock()
    adapter.extract_url.return_value = ('share', '', 'selected', [])
    adapter.get_stoken.return_value = {'data': {'stoken': 'token'}}
    executor = DramaTaskExecutor(adapter=adapter, task_data={
        'taskname': '测试剧', 'savepath': '/测试剧', 'addition': {'file_filter': 'does-not-match'},
    })
    executor._ensure_dest_dir_fid = Mock(return_value='dest')
    executor._ls_dir = Mock(return_value={'data': {'list': []}})
    adapter.get_detail.return_value = {"data": {"list": [
        {'fid': 'source-10', 'file_name': '测试剧 S01E10.mp4', 'size': 1024},
    ]}}
    executor._save_with_saved_fids = Mock(side_effect=AssertionError('Filtered files must not be saved'))
    executor.execute()
    assert executor.saved_latest is None
    executor._save_with_saved_fids.assert_not_called()
