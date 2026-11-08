from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.tasks import update_task
from app.services.task_share_links import (
    drop_task_share_link,
    get_task_share_link,
    merge_task_extra,
    save_task_share_link,
)


def _stored_task(**overrides):
    task = SimpleNamespace(
        id=1,
        taskname="测试剧",
        shareurl="https://pan.quark.cn/s/source",
        savepath="/剧集/测试剧",
        account_name="夸克1",
        extra_json=json.dumps({"auto_update_shareurl": True}),
    )
    for key, value in overrides.items():
        setattr(task, key, value)
    return task


class TaskShareLinkStorageTests(unittest.TestCase):
    def test_save_and_read_share_link(self) -> None:
        task = _stored_task()
        record = save_task_share_link(
            task,
            share_url="https://pan.quark.cn/s/result",
            share_id="share-1",
            title="测试剧",
            drive_type="quark",
            account_name="夸克1",
            passcode="abcd",
        )
        self.assertEqual(get_task_share_link(task)["share_url"], "https://pan.quark.cn/s/result")
        self.assertEqual(record["share_id"], "share-1")

    def test_merge_extra_keeps_existing_share_link(self) -> None:
        task = _stored_task()
        save_task_share_link(
            task,
            share_url="https://pan.quark.cn/s/result",
            drive_type="quark",
            account_name="夸克1",
        )
        merge_task_extra(task, {"auto_update_shareurl": False, "other_flag": 12})
        extra = json.loads(task.extra_json)
        self.assertEqual(extra["share_link"]["share_url"], "https://pan.quark.cn/s/result")
        self.assertFalse(extra["auto_update_shareurl"])
        self.assertEqual(extra["other_flag"], 12)

    def test_drop_share_link(self) -> None:
        task = _stored_task()
        save_task_share_link(task, share_url="https://pan.quark.cn/s/result")
        self.assertTrue(drop_task_share_link(task))
        self.assertIsNone(get_task_share_link(task))
        self.assertFalse(drop_task_share_link(task))


class TaskUpdateShareLinkTests(unittest.TestCase):
    def _run_update(self, task, **payload):
        db = Mock()
        with patch("app.services.tasks.get_task", return_value=task):
            update_task(db, 1, **payload)
        db.flush.assert_called_once()
        return json.loads(task.extra_json)

    def test_keeps_share_link_when_only_name_changes(self) -> None:
        task = _stored_task()
        save_task_share_link(task, share_url="https://pan.quark.cn/s/result")
        extra = self._run_update(task, taskname="测试剧 第二季", extra={"auto_update_shareurl": True})
        self.assertEqual(extra["share_link"]["share_url"], "https://pan.quark.cn/s/result")

    def test_keeps_share_link_when_shareurl_changes(self) -> None:
        task = _stored_task()
        save_task_share_link(task, share_url="https://pan.quark.cn/s/result")
        extra = self._run_update(task, shareurl="https://pan.quark.cn/s/another", extra={"auto_update_shareurl": True})
        self.assertEqual(extra["share_link"]["share_url"], "https://pan.quark.cn/s/result")

    def test_drops_share_link_when_savepath_changes(self) -> None:
        task = _stored_task()
        save_task_share_link(task, share_url="https://pan.quark.cn/s/result")
        extra = self._run_update(task, savepath="/剧集/另一个目录")
        self.assertNotIn("share_link", extra)

    def test_keeps_share_link_on_unrelated_update(self) -> None:
        task = _stored_task()
        save_task_share_link(task, share_url="https://pan.quark.cn/s/result")
        extra = self._run_update(task, enabled=False)
        self.assertEqual(extra["share_link"]["share_url"], "https://pan.quark.cn/s/result")


if __name__ == "__main__":
    unittest.main()
