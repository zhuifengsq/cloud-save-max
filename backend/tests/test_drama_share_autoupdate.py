from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.application.drama_share_autoupdate import resolve_drama_shareurl_update


def _task(**overrides):
    task = SimpleNamespace(
        id=1,
        task_type="drama",
        taskname="测试剧",
        shareurl="https://pan.quark.cn/s/oldshare",
        savepath="/剧集/测试剧",
        account_name="",
        tmdb_id=0,
        tmdb_media_type=None,
        extra_json=json.dumps({"auto_update_shareurl": True}),
        addition_json=json.dumps({}),
    )
    for key, value in overrides.items():
        setattr(task, key, value)
    return task


class DramaShareAutoUpdateTests(unittest.TestCase):
    def test_repairs_dead_link_and_keeps_folder_for_normal_mode(self) -> None:
        task = _task()
        suggestion = {"shareurl": "https://pan.quark.cn/s/newshare", "taskname": "测试剧", "datetime": "2026-10-27 12:00:00"}
        groups = [([{"file_name": "08.mp4", "updated_at": 100}], "fid-new", 100, "4k")]
        db = Mock()
        with (
            patch("app.application.drama_share_autoupdate._search_candidates", return_value=([suggestion], False, {})),
            patch("app.application.drama_share_autoupdate._prepare_candidates", return_value=([suggestion], {})),
            patch("app.application.share_preview_batch.fetch_share_file_list_grouped", return_value=groups),
        ):
            result = resolve_drama_shareurl_update(db, task)

        self.assertTrue(result["updated"])
        self.assertEqual(task.shareurl, "https://pan.quark.cn/s/newshare#/list/share/fid-new")
        db.flush.assert_called_once()

    def test_folder_diff_mode_keeps_share_root(self) -> None:
        task = _task(addition_json=json.dumps({"folder_mode": "folder_diff"}))
        suggestion = {"shareurl": "https://pan.quark.cn/s/newshare", "taskname": "测试剧"}
        groups = [([{"file_name": "08.mp4", "updated_at": 100}], "fid-new", 100, "4k")]
        db = Mock()
        with (
            patch("app.application.drama_share_autoupdate._search_candidates", return_value=([suggestion], False, {})),
            patch("app.application.drama_share_autoupdate._prepare_candidates", return_value=([suggestion], {})),
            patch("app.application.share_preview_batch.fetch_share_file_list_grouped", return_value=groups),
        ):
            result = resolve_drama_shareurl_update(db, task)

        self.assertTrue(result["updated"])
        self.assertEqual(task.shareurl, "https://pan.quark.cn/s/newshare")

    def test_no_previewable_candidate_keeps_old_link(self) -> None:
        task = _task()
        suggestion = {"shareurl": "https://pan.quark.cn/s/newshare", "taskname": "测试剧"}
        db = Mock()
        with (
            patch("app.application.drama_share_autoupdate._search_candidates", return_value=([suggestion], False, {})),
            patch("app.application.drama_share_autoupdate._prepare_candidates", return_value=([suggestion], {})),
            patch("app.application.share_preview_batch.fetch_share_file_list_grouped", return_value=[]),
        ):
            result = resolve_drama_shareurl_update(db, task)

        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "no_better_candidate")
        self.assertEqual(task.shareurl, "https://pan.quark.cn/s/oldshare")

    def test_disabled_toggle_skips(self) -> None:
        task = _task(extra_json=json.dumps({"auto_update_shareurl": False}))
        result = resolve_drama_shareurl_update(Mock(), task)
        self.assertFalse(result["checked"])
        self.assertEqual(result["reason"], "not_applicable")

    def test_same_link_is_not_applied(self) -> None:
        task = _task(shareurl="https://pan.quark.cn/s/newshare")
        suggestion = {"shareurl": "https://pan.quark.cn/s/newshare", "taskname": "测试剧"}
        groups = [([{"file_name": "08.mp4", "updated_at": 100}], "fid-new", 100, "4k")]
        with (
            patch("app.application.drama_share_autoupdate._search_candidates", return_value=([suggestion], False, {})),
            patch("app.application.drama_share_autoupdate._prepare_candidates", return_value=([suggestion], {})),
            patch("app.application.share_preview_batch.fetch_share_file_list_grouped", return_value=groups),
        ):
            result = resolve_drama_shareurl_update(Mock(), task)

        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "no_better_candidate")


if __name__ == "__main__":
    unittest.main()
