from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from starlette.requests import Request

from app.api.routes.tasks import post_create_task_share


class TaskShareRouteTests(unittest.TestCase):
    def test_uses_task_savepath_and_selected_account(self) -> None:
        task = SimpleNamespace(
            id=7, taskname="测试剧", savepath="/剧集/测试剧", account_name="夸克1",
            shareurl="https://pan.quark.cn/s/source",
        )
        account = SimpleNamespace(name="夸克1", drive_type="quark")
        adapter = Mock(is_active=True)
        adapter.create_share_link.return_value = {
            "share_url": "https://pan.quark.cn/s/result", "share_id": "share-1", "title": "测试剧",
        }
        manager = Mock()
        manager.get_adapter_for_task.return_value = adapter
        request = Request({"type": "http", "method": "POST", "path": "/api/tasks/7/share", "headers": [], "client": ("127.0.0.1", 1)})
        db = Mock()

        with (
            patch("app.api.routes.tasks.get_task", return_value=task),
            patch("app.api.routes.tasks.get_active_account", return_value=account),
            patch("app.api.routes.tasks.DatabaseAccountManager", return_value=manager),
            patch("app.api.routes.tasks.audit.write_audit_log"),
        ):
            result = post_create_task_share(request, 7, SimpleNamespace(user=SimpleNamespace(id=1)), db)

        self.assertEqual(result.share_url, "https://pan.quark.cn/s/result")
        self.assertEqual(result.account_name, "夸克1")
        adapter.create_share_link.assert_called_once_with("/剧集/测试剧", "测试剧")
        db.commit.assert_called_once()

    def test_persists_generated_link_on_task(self) -> None:
        task = SimpleNamespace(
            id=8, taskname="测试剧", savepath="/剧集/测试剧", account_name="夸克1",
            shareurl="https://pan.quark.cn/s/source", extra_json=json.dumps({"auto_update_shareurl": True}),
        )
        account = SimpleNamespace(name="夸克1", drive_type="quark")
        adapter = Mock(is_active=True)
        adapter.create_share_link.return_value = {
            "share_url": "https://pan.quark.cn/s/result",
            "share_id": "share-1",
            "title": "测试剧",
            "passcode": "abcd",
        }
        manager = Mock()
        manager.get_adapter_for_task.return_value = adapter
        request = Request({"type": "http", "method": "POST", "path": "/api/tasks/8/share", "headers": [], "client": ("127.0.0.1", 1)})
        db = Mock()

        with (
            patch("app.api.routes.tasks.get_task", return_value=task),
            patch("app.api.routes.tasks.get_active_account", return_value=account),
            patch("app.api.routes.tasks.DatabaseAccountManager", return_value=manager),
            patch("app.api.routes.tasks.audit.write_audit_log"),
        ):
            result = post_create_task_share(request, 8, SimpleNamespace(user=SimpleNamespace(id=1)), db)

        extra = json.loads(task.extra_json)
        self.assertEqual(extra["auto_update_shareurl"], True)
        stored = extra["share_link"]
        self.assertEqual(stored["share_url"], "https://pan.quark.cn/s/result")
        self.assertEqual(stored["share_id"], "share-1")
        self.assertEqual(stored["drive_type"], "quark")
        self.assertEqual(stored["account_name"], "夸克1")
        self.assertEqual(stored["passcode"], "abcd")
        self.assertTrue(stored["saved_at"])
        self.assertEqual(result.saved_at, stored["saved_at"])
        db.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
