from unittest.mock import Mock

import pytest

from app.extensions.runtime.drama_executor import DramaTaskExecutor
from app.extensions.runtime.execution_log import ExecutionLog


@pytest.mark.parametrize("location", ["normal", "root", "incremental", "whole_folder"])
@pytest.mark.parametrize(("rules", "log_message"), [
    ({"filter_words": "群"}, "关键词过滤：移除 1 个匹配的文件"),
    ({"file_filter": "后会友期|mp4"}, "文件筛选：移除 1 个不匹配的文件"),
    ({"min_size": "1KB"}, "大小过滤：移除 1 个低于 1KB 的文件"),
])
def test_drama_applies_file_filters_before_saving(location, rules, log_message):
    files = [
        {"fid": "ad", "fid_token": "ad-token", "file_name": "加群，获取更多资源.jpg", "size": 1},
        {"fid": "video", "fid_token": "video-token", "file_name": "后会友期 S01E01.mp4", "size": 1024},
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
    executor._fetch_share_items = Mock(side_effect=lambda **kwargs: share[kwargs["pdir_fid"]])
    executor._save_with_saved_fids = Mock(return_value=["saved-video"])

    executor.execute()

    executor._save_with_saved_fids.assert_called_once()
    plan = executor._save_with_saved_fids.call_args.kwargs["plan"]
    assert [item.fid for item in plan] == ["video"]
    assert log_message in log.render()
