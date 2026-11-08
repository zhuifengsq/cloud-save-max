# -*- coding: utf-8 -*-
"""追剧任务引流文件 —— 按需触发回归测试（契约第 7 节第 1 条）。

断言：即使总开关已开启、素材已配置，以下链路也绝不产生引流转存调用：
- 定时追更 / 手动执行 / 执行全部 / 重试：TaskExecutor.run_task（调度器与
  POST /tasks/{id}/run 都走这里；前端“执行全部”就是循环调用 run）；
- DramaTaskExecutor.execute 直接调用；
- 生成分享链接：POST /tasks/{id}/share 的 handler。

判定方式（三重）：
1. 执行链路交给适配器的 lead_in_* 方法调用次数为 0；
2. task_lead_in_records 表零新增；
3. 审计日志无 task.lead_in_add。

全部使用内存 sqlite + Mock 适配器，禁止真实网络。
"""

from __future__ import annotations

import json
import unittest
from contextlib import ExitStack
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from treelib import Tree

from app.api.routes.tasks import post_create_task_share, post_run_task
from app.core.deps import CurrentUser
from app.core.permissions import TASK_RUN, TASK_WRITE
from app.db.base import Base
from app.extensions.adapters.adapter_factory import AdapterFactory
from app.extensions.runtime.drama_executor import DramaTaskExecutor
from app.extensions.runtime.execution_log import ExecutionLog
from app.extensions.runtime.task_executor import TaskExecutor
from app.models.audit_log import AuditLog
from app.models.drive_account import DriveAccount
from app.models.task import Task
from app.models.task_execution import TaskExecution
from app.models.task_lead_in import TaskLeadInRecord
from app.services import drama_lead_in as svc
from app.services.task_share_links import get_task_share_link

# 触发所有模型注册到 Base.metadata（sqlite 建表用）。
import app.models  # noqa: F401,E402

SHARE_URL = "https://pan.example.com/s/source"
MATERIAL_SHARE_URL = "https://pan.example.com/s/abcdef"
MATERIAL_PASSCODE = "abcd"
MATERIAL_FID = "mat-1"
MATERIAL_NAME = "第01集.mp4"
DIR_PATH = "/追剧/测试剧"
LEAD_IN_METHODS = (
    "lead_in_resolve_material",
    "lead_in_resolve_dir",
    "lead_in_inspect",
    "lead_in_add",
    "lead_in_verify",
)


class FakeDramaExecutor:
    """替代真实 DramaTaskExecutor：返回成功树，并记录执行次数。"""

    instances: list["FakeDramaExecutor"] = []
    fail_times = 0

    def __init__(self, *, adapter, task_data, log):
        self.adapter = adapter
        self.task_data = task_data
        self.log = log
        self.saved_latest = {}
        FakeDramaExecutor.instances.append(self)

    def execute(self):
        if FakeDramaExecutor.fail_times > 0:
            FakeDramaExecutor.fail_times -= 1
            self.log.set_stage("get_stoken")
            raise RuntimeError("模拟网盘临时失败")
        tree = Tree()
        tree.create_node("完成", "root")
        return tree


class FakeShareDrive:
    """只用于让分享接口通过“适配器类实现了 create_share_link”的能力判断。"""

    DRIVE_TYPE = "mockdrive"
    DRIVE_NAME = "测试网盘"
    SUPPORTS_DRAMA_LEAD_IN = True

    @classmethod
    def resolve_runtime_config(cls, config, cookie):
        return dict(config or {})

    @classmethod
    def get_config_meta(cls):
        return {"format": "raw", "fields": []}

    def __init__(self, cookie="", index=0, config=None, *, account_name="", no_login=False):
        self.cookie = cookie
        self.account_name = account_name

    def create_share_link(self, savepath, taskname):
        return {
            "share_url": "https://pan.example.com/s/generated",
            "share_id": "share-1",
            "title": taskname,
            "passcode": "9999",
            "expires_in_days": 7,
        }


class FakeAccountManager:
    def __init__(self, db, adapter):
        self.db = db
        self.adapter = adapter

    def init_for_tasks(self, tasks):
        return None

    def get_adapter_for_task(self, task_data):
        return self.adapter


class DramaLeadInTriggerRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        self.account = DriveAccount(
            name="mock-account",
            drive_type="mockdrive",
            cookie="cookie-value",
            config_json=json.dumps({"cookie": "cookie-value"}),
            enabled=True,
            is_default=True,
            runtime_status="active",
        )
        self.task = Task(
            task_uid="t-run",
            task_type="drama",
            taskname="测试剧",
            shareurl=SHARE_URL,
            savepath="追剧/测试剧",
            account_name="mock-account",
            enabled=True,
        )
        self.task2 = Task(
            task_uid="t-run-2",
            task_type="drama",
            taskname="测试剧2",
            shareurl=SHARE_URL,
            savepath="追剧/测试剧2",
            account_name="mock-account",
            enabled=True,
        )
        self.db.add_all([self.account, self.task, self.task2])
        self.db.commit()

        # 开启总开关并配置素材：保证执行/分享链路是在“功能已开启”的前提下被回归验证。
        svc._set_setting(self.db, svc.SETTING_KEY_ENABLED, "1")
        svc._set_setting(
            self.db,
            svc.SETTING_KEY_MATERIALS,
            json.dumps(
                {
                    "mockdrive": {
                        "shareurl": MATERIAL_SHARE_URL,
                        "passcode": MATERIAL_PASSCODE,
                        "file_fid": MATERIAL_FID,
                        "file_name": MATERIAL_NAME,
                        "config_version": 1,
                        "updated_at": "2026-11-06T00:00:00",
                    }
                },
                ensure_ascii=False,
            ),
        )
        self.db.commit()
        self.assertTrue(svc.get_drama_lead_in_enabled(self.db))
        self.assertIsNotNone(svc.get_drama_lead_in_material(self.db, "mockdrive"))

        AdapterFactory.register_adapter("mockdrive", FakeShareDrive)
        AdapterFactory.clear_cache()
        FakeDramaExecutor.instances.clear()
        FakeDramaExecutor.fail_times = 0

    def tearDown(self) -> None:
        AdapterFactory.ADAPTER_MAP.pop("mockdrive", None)
        AdapterFactory.clear_cache()
        FakeDramaExecutor.fail_times = 0
        self.db.close()
        self.engine.dispose()

    # ---------- 辅助 ----------
    def _recording_adapter(self) -> Mock:
        adapter = Mock()
        adapter.is_active = True
        adapter.nickname = "测试"
        adapter.DRIVE_TYPE = "mockdrive"
        adapter.extract_url.return_value = ("share-id", "", "root", [])
        adapter.get_stoken.return_value = {"status": 200, "data": {"stoken": "stoken"}}
        adapter.get_detail.return_value = {"data": {"list": []}}
        return adapter

    def _assert_no_lead_in_trigger(self, adapter: Mock) -> None:
        for name in LEAD_IN_METHODS:
            self.assertEqual(
                getattr(adapter, name).call_count,
                0,
                f"执行/分享链路不得调用适配器 {name}",
            )
        records = self.db.scalars(select(TaskLeadInRecord)).all()
        self.assertEqual(records, [], "执行/分享链路不得写入 task_lead_in_records")
        actions = [
            row.action
            for row in self.db.scalars(select(AuditLog).order_by(AuditLog.id)).all()
        ]
        self.assertNotIn("task.lead_in_add", actions, "执行/分享链路不得写引流转存审计")
        return None

    def _run_task(self, adapter: Mock, task: Task) -> TaskExecution:
        manager = SimpleNamespace(
            init_for_tasks=lambda _: None,
            get_adapter_for_task=lambda _: adapter,
        )
        with ExitStack() as stack:
            stack.enter_context(
                patch("app.extensions.runtime.task_executor.DramaTaskExecutor", FakeDramaExecutor)
            )
            stack.enter_context(
                patch("app.extensions.runtime.task_executor.check_generated_share", return_value=None)
            )
            stack.enter_context(
                patch("app.services.magic_regex.get_enabled_magic_regex_map", return_value={})
            )
            stack.enter_context(patch("time.sleep"))
            execution = TaskExecutor(self.db).run_task(task, account_manager=manager)
            self.db.commit()
        return execution

    def _request(self) -> SimpleNamespace:
        return SimpleNamespace(
            client=SimpleNamespace(host="127.0.0.1"),
            headers={"user-agent": "qa-regression"},
            method="POST",
            url="http://testserver/api/tasks",
        )

    # ---------- 定时追更 / 手动执行 / 执行全部 / 重试 ----------
    def test_drama_manual_and_scheduled_run_never_trigger_lead_in(self) -> None:
        adapter = self._recording_adapter()

        execution = self._run_task(adapter, self.task)

        self.assertEqual(execution.status, "success")
        self.assertEqual(len(FakeDramaExecutor.instances), 1)
        self._assert_no_lead_in_trigger(adapter)

    def test_execute_all_loops_run_without_lead_in(self) -> None:
        adapter = self._recording_adapter()

        results = [self._run_task(adapter, task) for task in (self.task, self.task2)]

        self.assertEqual([item.status for item in results], ["success", "success"])
        self.assertEqual(len(FakeDramaExecutor.instances), 2)
        self._assert_no_lead_in_trigger(adapter)

    def test_retry_after_failure_never_triggers_lead_in(self) -> None:
        adapter = self._recording_adapter()
        FakeDramaExecutor.fail_times = 1

        first = self._run_task(adapter, self.task)
        self.assertEqual(first.status, "failed")
        self._assert_no_lead_in_trigger(adapter)

        second = self._run_task(adapter, self.task)
        self.assertEqual(second.status, "success")
        self._assert_no_lead_in_trigger(adapter)

    def test_run_route_handler_does_not_touch_lead_in(self) -> None:
        calls: list[int] = []

        class RecordingExecutor:
            def __init__(self, db):
                self.db = db

            def run_task(self, task, **kwargs):
                calls.append(int(task.id))
                execution = TaskExecution(
                    task_id=int(task.id),
                    status="success",
                    started_at=datetime.now(),
                    finished_at=datetime.now(),
                    message="执行完成",
                    stage="end",
                    run_log="ok",
                )
                self.db.add(execution)
                self.db.flush()
                return execution

        current = CurrentUser(user=SimpleNamespace(id=7), roles=[], permissions={TASK_RUN})
        with (
            patch("app.api.routes.tasks.TaskExecutor", RecordingExecutor),
            patch("app.api.routes.tasks.build_task_section", return_value=(None, False)),
            patch("app.api.routes.tasks.send_runtime"),
        ):
            out1 = post_run_task(self._request(), int(self.task.id), current=current, db=self.db)
            out2 = post_run_task(self._request(), int(self.task2.id), current=current, db=self.db)

        self.assertEqual(calls, [int(self.task.id), int(self.task2.id)])
        self.assertEqual(out1.status, "success")
        self.assertEqual(out2.status, "success")
        self._assert_no_lead_in_trigger(Mock())

    # ---------- DramaTaskExecutor 直接执行 ----------
    def test_drama_executor_execute_does_not_touch_lead_in(self) -> None:
        adapter = Mock()
        adapter.extract_url.return_value = ("share", "", "selected", [])
        adapter.get_stoken.return_value = {"status": 200, "data": {"stoken": "token"}}
        log = ExecutionLog()
        executor = DramaTaskExecutor(
            adapter=adapter,
            task_data={
                "taskname": "测试剧",
                "savepath": DIR_PATH,
                "addition": {},
            },
            log=log,
        )
        executor._ensure_dest_dir_fid = Mock(side_effect=lambda path: f"dest:{path}")
        executor._ls_dir = Mock(return_value={"data": {"list": []}})
        executor._fetch_share_items = Mock(return_value=[])
        executor._save_with_saved_fids = Mock(return_value=[])

        executor.execute()

        executor._save_with_saved_fids.assert_not_called()
        for name in LEAD_IN_METHODS:
            self.assertEqual(getattr(adapter, name).call_count, 0)
        self.assertEqual(self.db.scalars(select(TaskLeadInRecord)).all(), [])

    # ---------- 生成分享链接 ----------
    def test_share_creation_does_not_touch_lead_in_or_leak_material(self) -> None:
        adapter = Mock()
        adapter.is_active = True
        adapter.create_share_link.return_value = {
            "share_url": "https://pan.example.com/s/generated",
            "share_id": "share-1",
            "title": "测试剧",
            "passcode": "9999",
            "expires_in_days": 7,
        }
        current = CurrentUser(user=SimpleNamespace(id=7), roles=[], permissions={TASK_WRITE})

        with patch(
            "app.api.routes.tasks.DatabaseAccountManager",
            side_effect=lambda db: FakeAccountManager(db, adapter),
        ):
            out = post_create_task_share(
                self._request(), int(self.task.id), current=current, db=self.db
            )

        self.assertEqual(out.share_url, "https://pan.example.com/s/generated")
        self.assertEqual(adapter.create_share_link.call_count, 1)
        adapter.create_share_link.assert_called_once_with(self.task.savepath, self.task.taskname)
        for name in LEAD_IN_METHODS:
            self.assertEqual(getattr(adapter, name).call_count, 0)
        self.assertEqual(self.db.scalars(select(TaskLeadInRecord)).all(), [])

        # 生成结果已落盘，普通查看接口可直接取回（老功能回归）。
        saved = get_task_share_link(self.task)
        self.assertEqual(saved["share_url"], "https://pan.example.com/s/generated")

        # 审计与响应不得泄露引流素材的提取码 / 完整私有链接。
        audits = self.db.scalars(select(AuditLog).order_by(AuditLog.id)).all()
        actions = [row.action for row in audits]
        self.assertIn("task.share.create", actions)
        self.assertNotIn("task.lead_in_add", actions)
        for row in audits:
            detail = str(row.detail or "")
            self.assertNotIn(MATERIAL_PASSCODE, detail)
            self.assertNotIn(MATERIAL_SHARE_URL, detail)
        self.assertNotIn(MATERIAL_PASSCODE, json.dumps(out.model_dump(), ensure_ascii=False, default=str))
        self.assertNotIn(MATERIAL_SHARE_URL, json.dumps(out.model_dump(), ensure_ascii=False, default=str))


if __name__ == "__main__":
    unittest.main()
