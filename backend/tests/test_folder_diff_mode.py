from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from treelib import Tree

from app.extensions.runtime.drama_executor import DramaTaskExecutor


def _executor(addition: dict) -> DramaTaskExecutor:
    adapter = Mock()
    task_data = {
        "taskname": "测试剧",
        "pattern": "",
        "replace": "",
        "addition": addition,
        "extra": {},
        "magic_regex": {},
        "ignore_extension": True,
    }
    return DramaTaskExecutor(adapter=adapter, task_data=task_data)


def _run(executor: DramaTaskExecutor, *, share_items, children, dest_dirs, existing_names, dest_listing=None):
    tree = Tree()
    tree.create_node("root", "root")
    executor._ensure_dest_dir_fid = Mock(side_effect=lambda path: f"dest:{path}")
    executor._list_dest_names = Mock(return_value=set(existing_names))
    executor._fetch_share_items = Mock(side_effect=lambda **kwargs: children.get(kwargs["pdir_fid"], []))
    executor._save_with_saved_fids = Mock(return_value=["saved-fid"])
    executor._execute_folder_diff(
        share_items=share_items,
        root_files=[],
        pwd_id="pwd",
        stoken="stoken",
        dest_root_fid="root-dest",
        dest_file_list=dest_listing or [],
        dest_dir_map=dest_dirs,
        savepath="/tv/测试剧",
        ignore_extension=True,
        tree=tree,
    )
    return executor


class FolderDiffModeTests(unittest.TestCase):
    def test_whitelist_folder_only_transfers_new_files(self) -> None:
        executor = _executor({"folder_mode": "folder_diff", "whitelist_folders": "4k"})
        share_items = [{"fid": "d-4k", "fid_token": "t-4k", "file_name": "4k", "dir": True}]
        children = {
            "d-4k": [
                {"fid": "f-a", "fid_token": "ta", "file_name": "a.mp4", "dir": False},
                {"fid": "f-b", "fid_token": "tb", "file_name": "b.mp4", "dir": False},
            ]
        }
        _run(executor, share_items=share_items, children=children, dest_dirs={}, existing_names={"a"})

        executor._save_with_saved_fids.assert_called_once()
        kwargs = executor._save_with_saved_fids.call_args.kwargs
        self.assertEqual([item.origin_name for item in kwargs["plan"]], ["b.mp4"])
        self.assertEqual(kwargs["dest_root_fid"], "dest:/tv/测试剧/4k")

    def test_other_folder_missing_in_dest_saved_as_whole_folder(self) -> None:
        executor = _executor({"folder_mode": "folder_diff", "whitelist_folders": "4k"})
        share_items = [{"fid": "d-extra", "fid_token": "t-extra", "file_name": "花絮", "dir": True}]
        _run(executor, share_items=share_items, children={}, dest_dirs={}, existing_names=set())

        executor._save_with_saved_fids.assert_called_once()
        kwargs = executor._save_with_saved_fids.call_args.kwargs
        self.assertEqual([item.fid for item in kwargs["plan"]], ["d-extra"])
        self.assertEqual(kwargs["dest_root_fid"], "root-dest")

    def test_other_folder_already_in_dest_is_skipped(self) -> None:
        executor = _executor({"folder_mode": "folder_diff", "whitelist_folders": "4k"})
        share_items = [{"fid": "d-extra", "fid_token": "t-extra", "file_name": "花絮", "dir": True}]
        _run(executor, share_items=share_items, children={}, dest_dirs={"花絮": "dest-extra"}, existing_names=set())

        executor._save_with_saved_fids.assert_not_called()

    def test_whitelist_folder_records_saved_latest(self) -> None:
        executor = _executor({"folder_mode": "folder_diff", "whitelist_folders": "4k"})
        share_items = [{"fid": "d-4k", "fid_token": "t-4k", "file_name": "4k", "dir": True}]
        children = {
            "d-4k": [
                {"fid": "f-a", "fid_token": "ta", "file_name": "Show.S01E08.mp4", "dir": False},
            ]
        }
        _run(executor, share_items=share_items, children=children, dest_dirs={}, existing_names=set())

        self.assertIsNotNone(executor.saved_latest)
        self.assertEqual(executor.saved_latest["episode"], 8)
        self.assertEqual(executor.saved_latest["name"], "Show.S01E08.mp4")

    def test_excluded_folder_is_ignored(self) -> None:
        executor = _executor(
            {"folder_mode": "folder_diff", "whitelist_folders": "4k", "folder_exclude": "花絮"}
        )
        share_items = [{"fid": "d-extra", "fid_token": "t-extra", "file_name": "花絮", "dir": True}]
        _run(executor, share_items=share_items, children={}, dest_dirs={}, existing_names=set())

        executor._save_with_saved_fids.assert_not_called()
        executor._ensure_dest_dir_fid.assert_not_called()


if __name__ == "__main__":
    unittest.main()
