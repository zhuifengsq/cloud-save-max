"""追剧任务引流文件后端核心单测（配置/预览/确认/记录/任务字段）。

全部使用 Mock 适配器，不依赖任何真实网盘。
"""

from __future__ import annotations

import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api.routes import system_settings_drama_lead_in as settings_route
from app.api.routes import task_lead_in as task_lead_in_route
from app.core.errors import ApiError
from app.infrastructure.persistence.base import Base
from app.infrastructure.drive.factory import AdapterFactory
from app.infrastructure.persistence.models.audit_log import AuditLog
from app.infrastructure.persistence.models.drive_account import DriveAccount
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.models.task_lead_in import TaskLeadInRecord
from app.schemas.drama_lead_in import DramaLeadInConfirmIn
from app.application import drama_lead_in as svc
from app.api.presenters.task import task_out

DIR_PATH = "/追剧/测试剧"
MATERIAL_NAME = "第01集.mp4"


class MockLeadInAdapterClass:
    """声明支持引流的测试适配器（只提供注册表需要的类属性）。"""

    DRIVE_TYPE = "mockdrive"
    DRIVE_NAME = "测试网盘"
    SUPPORTS_DRAMA_LEAD_IN = True
    CONFIG_FORMAT = "raw"
    default_config = {"cookie": ""}
    config_fields = [
        {
            "key": "cookie",
            "label": "Cookie",
            "description": "",
            "input_type": "textarea",
            "required": True,
            "secret": True,
            "placeholder": "",
        }
    ]

    @classmethod
    def get_config_meta(cls):
        return {
            "drive_name": cls.DRIVE_NAME,
            "config_format": cls.CONFIG_FORMAT,
            "default_config": dict(cls.default_config),
            "config_fields": list(cls.config_fields),
        }


class MockDisabledAdapterClass:
    """未声明引流能力的测试适配器。"""

    DRIVE_TYPE = "mockdisabled"
    DRIVE_NAME = "不支持网盘"
    SUPPORTS_DRAMA_LEAD_IN = False
    CONFIG_FORMAT = "raw"
    default_config = {"cookie": ""}
    config_fields = []

    @classmethod
    def get_config_meta(cls):
        return {
            "drive_name": cls.DRIVE_NAME,
            "config_format": cls.CONFIG_FORMAT,
            "default_config": dict(cls.default_config),
            "config_fields": [],
        }


class DramaLeadInCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        AdapterFactory.register_adapter("mockdrive", MockLeadInAdapterClass)
        AdapterFactory.register_adapter("mockdisabled", MockDisabledAdapterClass)
        self.addCleanup(AdapterFactory.ADAPTER_MAP.pop, "mockdrive", None)
        self.addCleanup(AdapterFactory.ADAPTER_MAP.pop, "mockdisabled", None)

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
            task_uid="t-1",
            task_type="drama",
            taskname="测试剧",
            shareurl="https://pan.example.com/s/source",
            savepath="追剧/测试剧",
            account_name="mock-account",
            enabled=True,
        )
        self.db.add_all([self.account, self.task])
        self.db.commit()

        self.adapter = Mock()
        self.adapter.ensure_initialized.return_value = None
        self.adapter.lead_in_resolve_dir.return_value = {
            "code": 0,
            "message": "",
            "data": {"dir": {"fid": "dir1", "path": DIR_PATH, "name": "测试剧"}, "entries": []},
        }
        self.adapter.lead_in_resolve_material.return_value = {
            "code": 0,
            "message": "",
            "data": {
                "files": [
                    {"fid": "mat1", "name": MATERIAL_NAME, "size": 1024, "dir": False, "path": "/" + MATERIAL_NAME}
                ],
                "material": {"fid": "mat1", "name": MATERIAL_NAME, "size": 1024, "path": "/" + MATERIAL_NAME},
            },
        }
        self.adapter.lead_in_inspect.return_value = {
            "code": 0,
            "message": "",
            "data": {"state": "absent", "existing": None, "message": ""},
        }
        self.adapter.lead_in_add.return_value = {"code": 0, "message": "", "data": {"handle": {"id": "h1"}}}
        self.adapter.lead_in_verify.return_value = {
            "code": 0,
            "message": "",
            "data": {"state": "added", "fid": "file-1", "message": "已添加"},
        }
        self.create_patcher = patch.object(AdapterFactory, "create_adapter", return_value=self.adapter)
        self.create_patcher.start()
        self.addCleanup(self.create_patcher.stop)

        self._configure_material()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _materials(self, *, config_version: int = 1, passcode: str = "abcd") -> dict:
        return {
            "mockdrive": {
                "shareurl": "https://pan.example.com/s/abcdef",
                "passcode": passcode,
                "file_fid": "mat1",
                "file_name": MATERIAL_NAME,
                "config_version": config_version,
                "updated_at": "2026-11-06T20:00:00+08:00",
            }
        }

    def _configure_material(self, *, config_version: int = 1, passcode: str = "abcd") -> None:
        svc._set_setting(self.db, svc.SETTING_KEY_ENABLED, "1", "测试")
        svc._set_setting(
            self.db,
            svc.SETTING_KEY_MATERIALS,
            json.dumps(self._materials(config_version=config_version, passcode=passcode), ensure_ascii=False),
            "测试",
        )
        self.db.commit()

    def _preview_token(self) -> str:
        preview = svc.build_task_lead_in_preview(self.db, self.task)
        self.assertTrue(preview["available"], preview.get("reason"))
        token = preview["preview_token"]
        self.assertTrue(token)
        return token

    def _request(self, path: str = "/api/tasks/1/lead-in/preview") -> Request:
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": path,
                "headers": [],
                "client": ("127.0.0.1", 1),
            }
        )

    def _current_user(self) -> SimpleNamespace:
        return SimpleNamespace(user=SimpleNamespace(id=1))

    # ------------------------------------------------------------------
    # 配置：GET 脱敏 + drives 动态生成
    # ------------------------------------------------------------------

    def test_get_settings_masks_shareurl_and_lists_dynamic_drives(self) -> None:
        payload = svc.build_settings_payload(self.db)
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["version"], 1)
        drives = {item["drive_type"]: item for item in payload["drives"]}
        self.assertIn("mockdrive", drives)
        self.assertIn("mockdisabled", drives)
        # 注册表里的真实网盘也必须在（禁止硬编码）
        self.assertGreater(len(drives), 2)
        mockdrive = drives["mockdrive"]
        self.assertTrue(mockdrive["supported"])
        self.assertTrue(mockdrive["configured"])
        self.assertTrue(mockdrive["passcode_set"])
        self.assertEqual(mockdrive["shareurl"], "https://pan.example.com/s/abc***")
        self.assertNotIn("abcdef", mockdrive["shareurl"])
        self.assertNotIn("passcode", mockdrive)
        disabled = drives["mockdisabled"]
        self.assertFalse(disabled["supported"])
        self.assertTrue(disabled["reason"])

        out = settings_route.get_drama_lead_in_settings(self.db)
        self.assertTrue(out.enabled)
        self.assertEqual(len(out.drives), len(drives))

    # ------------------------------------------------------------------
    # 配置：PUT 真实解析 + 版本递增 + passcode 保持
    # ------------------------------------------------------------------

    def test_put_settings_resolves_material_and_keeps_passcode(self) -> None:
        result = svc.update_drama_lead_in_settings(
            self.db,
            {
                "enabled": True,
                "materials": [
                    {
                        "drive_type": "mockdrive",
                        "shareurl": "https://pan.example.com/s/newurl",
                        "passcode": "",
                        "file_fid": "mat1",
                        "file_name": "",
                    }
                ],
            },
        )
        self.adapter.lead_in_resolve_material.assert_called_with(
            "https://pan.example.com/s/newurl", "abcd", "mat1"
        )
        drive = {item["drive_type"]: item for item in result["drives"]}["mockdrive"]
        self.assertEqual(drive["config_version"], 2)
        self.assertEqual(drive["file_name"], MATERIAL_NAME)
        stored = svc.get_drama_lead_in_materials(self.db)["mockdrive"]
        self.assertEqual(stored["config_version"], 2)
        self.assertEqual(stored["passcode"], "abcd")

        result2 = svc.update_drama_lead_in_settings(
            self.db,
            {
                "enabled": True,
                "materials": [
                    {
                        "drive_type": "mockdrive",
                        "shareurl": "https://pan.example.com/s/newurl",
                        "passcode": "new-code",
                        "file_fid": "mat1",
                        "file_name": MATERIAL_NAME,
                    }
                ],
            },
        )
        drive2 = {item["drive_type"]: item for item in result2["drives"]}["mockdrive"]
        self.assertEqual(drive2["config_version"], 3)
        stored2 = svc.get_drama_lead_in_materials(self.db)["mockdrive"]
        self.assertEqual(stored2["passcode"], "new-code")

    def test_put_settings_rejects_unsupported_drive(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            svc.update_drama_lead_in_settings(
                self.db,
                {
                    "enabled": True,
                    "materials": [
                        {
                            "drive_type": "mockdisabled",
                            "shareurl": "https://pan.example.com/s/abc",
                            "passcode": "",
                            "file_fid": "f1",
                            "file_name": "a.mp4",
                        }
                    ],
                },
            )
        self.assertEqual(ctx.exception.code, "LEAD_IN_UNSUPPORTED")
        self.assertEqual(ctx.exception.http_status, 400)

    # ------------------------------------------------------------------
    # 扫描
    # ------------------------------------------------------------------

    def test_scan_returns_files_for_ambiguous_material(self) -> None:
        self.adapter.lead_in_resolve_material.return_value = {
            "code": "LEAD_IN_MATERIAL_AMBIGUOUS",
            "message": "分享内包含多个文件",
            "data": {
                "files": [
                    {"fid": "f1", "name": "a.mp4", "size": 1},
                    {"fid": "f2", "name": "b.mp4", "size": 2},
                ],
                "material": None,
            },
        }
        result = svc.scan_drama_lead_in_material(
            self.db, "mockdrive", "https://pan.example.com/s/abc", "abcd"
        )
        self.assertEqual(len(result["files"]), 2)
        self.assertEqual(result["selected_fid"], "")
        self.assertEqual(result["files"][0]["fid"], "f1")

    # ------------------------------------------------------------------
    # 预览
    # ------------------------------------------------------------------

    def test_preview_returns_contract_fields_and_signed_token(self) -> None:
        preview = svc.build_task_lead_in_preview(self.db, self.task)
        self.assertTrue(preview["available"])
        self.assertEqual(preview["reason"], "")
        self.assertEqual(preview["drive"]["drive_type"], "mockdrive")
        self.assertEqual(preview["account"]["account_name"], "mock-account")
        self.assertEqual(preview["directory"]["path"], DIR_PATH)
        self.assertEqual(preview["directory"]["fid"], "dir1")
        self.assertTrue(preview["directory"]["exists"])
        self.assertEqual(preview["material"], {"name": MATERIAL_NAME, "fid": "mat1"})
        self.assertEqual(preview["state"], "absent")
        self.assertIsNone(preview["last_added_at"])
        self.assertGreater(preview["expires_at"].timestamp(), time.time())

        payload = svc.parse_preview_token(preview["preview_token"])
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["task_id"], int(self.task.id))
        self.assertEqual(payload["drive_type"], "mockdrive")
        self.assertEqual(payload["account_name"], "mock-account")
        self.assertEqual(payload["dir_path"], DIR_PATH)
        self.assertEqual(payload["dir_fid"], "dir1")
        self.assertEqual(payload["material_fid"], "mat1")
        self.assertEqual(payload["material_name"], MATERIAL_NAME)
        self.assertEqual(payload["config_version"], 1)

    def test_preview_disabled_when_switch_off(self) -> None:
        svc._set_setting(self.db, svc.SETTING_KEY_ENABLED, "0", "测试")
        self.db.commit()
        preview = svc.build_task_lead_in_preview(self.db, self.task)
        self.assertFalse(preview["available"])
        self.assertEqual(preview["reason"], "引流功能已关闭")

    def test_preview_reason_when_material_not_configured(self) -> None:
        svc._set_setting(self.db, svc.SETTING_KEY_MATERIALS, "{}", "测试")
        self.db.commit()
        preview = svc.build_task_lead_in_preview(self.db, self.task)
        self.assertFalse(preview["available"])
        self.assertEqual(preview["reason"], "该网盘未配置引流素材")

    def test_preview_reason_when_directory_missing(self) -> None:
        self.adapter.lead_in_resolve_dir.return_value = {
            "code": "LEAD_IN_DIR_NOT_FOUND",
            "message": "目录不存在",
            "data": None,
        }
        preview = svc.build_task_lead_in_preview(self.db, self.task)
        self.assertFalse(preview["available"])
        self.assertEqual(preview["reason"], "任务保存目录不存在")

    def test_preview_reason_when_drive_unsupported(self) -> None:
        task = Task(
            task_uid="t-2",
            task_type="drama",
            taskname="未知网盘剧",
            shareurl="https://example.com/s/abc",
            savepath="追剧/未知",
            account_name=None,
            enabled=True,
        )
        self.db.add(task)
        self.db.commit()
        preview = svc.build_task_lead_in_preview(self.db, task)
        self.assertFalse(preview["available"])
        self.assertEqual(preview["reason"], "该网盘暂不支持引流")

    # ------------------------------------------------------------------
    # 确认
    # ------------------------------------------------------------------

    def test_confirm_added_writes_record_and_audit(self) -> None:
        token = self._preview_token()
        result = svc.confirm_task_lead_in(
            self.db, self.task, token, actor_user_id=1, ip="127.0.0.1", user_agent="test"
        )
        self.assertEqual(result["status"], "added")
        self.assertGreater(result["record_id"], 0)
        self.assertIsNotNone(result["added_at"])
        self.adapter.lead_in_add.assert_called_once()
        self.adapter.lead_in_verify.assert_called_once()

        record = (
            self.db.query(TaskLeadInRecord)
            .filter(TaskLeadInRecord.task_id == int(self.task.id))
            .one()
        )
        self.assertEqual(record.status, "added")
        self.assertEqual(record.material_name, MATERIAL_NAME)
        self.assertEqual(record.config_version, 1)

        audit_rows = self.db.query(AuditLog).filter(AuditLog.action == "task.lead_in_add").all()
        self.assertEqual(len(audit_rows), 1)
        detail = json.loads(audit_rows[0].detail)
        self.assertEqual(detail["status"], "added")
        self.assertNotIn("passcode", detail)
        self.assertNotIn("shareurl", detail)

    def test_confirm_exists_does_not_add(self) -> None:
        token = self._preview_token()
        self.adapter.lead_in_inspect.return_value = {
            "code": 0,
            "message": "",
            "data": {"state": "exists", "existing": {"fid": "x"}, "message": ""},
        }
        result = svc.confirm_task_lead_in(self.db, self.task, token)
        self.assertEqual(result["status"], "exists")
        self.adapter.lead_in_add.assert_not_called()
        self.adapter.lead_in_verify.assert_not_called()

    def test_confirm_rejects_stale_preview(self) -> None:
        token = self._preview_token()
        self._configure_material(config_version=2, passcode="abcd")
        with self.assertRaises(ApiError) as ctx:
            svc.confirm_task_lead_in(self.db, self.task, token)
        self.assertEqual(ctx.exception.code, "LEAD_IN_STALE_PREVIEW")
        self.assertEqual(ctx.exception.http_status, 409)

        with self.assertRaises(ApiError) as ctx2:
            svc.confirm_task_lead_in(self.db, self.task, "v1.bad.bad")
        self.assertEqual(ctx2.exception.code, "LEAD_IN_STALE_PREVIEW")

    def test_confirm_rejects_concurrent_same_target(self) -> None:
        token = self._preview_token()
        lock = svc.lead_in_lock("mockdrive", "mock-account", DIR_PATH, MATERIAL_NAME)
        self.assertTrue(lock.acquire(blocking=False))
        try:
            with self.assertRaises(ApiError) as ctx:
                svc.confirm_task_lead_in(self.db, self.task, token)
            self.assertEqual(ctx.exception.code, "LEAD_IN_IN_PROGRESS")
            self.assertEqual(ctx.exception.http_status, 409)
        finally:
            lock.release()

    def test_confirm_reports_pending_when_verify_times_out(self) -> None:
        token = self._preview_token()
        self.adapter.lead_in_verify.side_effect = TimeoutError("timeout")
        result = svc.confirm_task_lead_in(self.db, self.task, token)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["message"], "网盘响应超时，添加结果待确认")
        record = (
            self.db.query(TaskLeadInRecord)
            .filter(TaskLeadInRecord.task_id == int(self.task.id))
            .one()
        )
        self.assertEqual(record.status, "pending")

    # ------------------------------------------------------------------
    # 记录与任务列表字段
    # ------------------------------------------------------------------

    def test_load_summary_map_uses_single_query(self) -> None:
        second = Task(
            task_uid="t-3",
            task_type="drama",
            taskname="第二部",
            shareurl="https://pan.example.com/s/second",
            savepath="追剧/第二部",
            account_name="mock-account",
            enabled=True,
        )
        self.db.add(second)
        self.db.flush()
        self.db.add_all(
            [
                TaskLeadInRecord(
                    task_id=int(self.task.id),
                    drive_type="mockdrive",
                    account_name="mock-account",
                    dir_path=DIR_PATH,
                    material_name=MATERIAL_NAME,
                    material_fid="mat1",
                    status="added",
                    message="",
                    config_version=1,
                ),
                TaskLeadInRecord(
                    task_id=int(second.id),
                    drive_type="mockdrive",
                    account_name="mock-account",
                    dir_path="/追剧/第二部",
                    material_name=MATERIAL_NAME,
                    material_fid="mat1",
                    status="conflict",
                    message="同名冲突",
                    config_version=1,
                ),
            ]
        )
        self.db.commit()

        statements: list[str] = []

        def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)

        event.listen(self.engine, "before_cursor_execute", _before_cursor_execute)
        self.addCleanup(event.remove, self.engine, "before_cursor_execute", _before_cursor_execute)

        summary = svc.load_lead_in_summary_map(self.db, [int(self.task.id), int(second.id)])
        selects = [
            item
            for item in statements
            if "task_lead_in_records" in item and item.lstrip().upper().startswith("SELECT")
        ]
        self.assertEqual(len(selects), 1)
        self.assertEqual(summary[int(self.task.id)]["state"], "added")
        self.assertIsNotNone(summary[int(self.task.id)]["last_added_at"])
        self.assertEqual(summary[int(second.id)]["state"], "conflict")
        self.assertIsNone(summary[int(second.id)]["last_added_at"])

    def test_task_out_includes_lead_in_summary(self) -> None:
        empty = task_out(self.db, self.task, executions=[])
        self.assertEqual(empty.lead_in.state, "none")
        self.assertIsNone(empty.lead_in.last_added_at)

        token = self._preview_token()
        svc.confirm_task_lead_in(self.db, self.task, token)
        summary = svc.load_lead_in_summary_map(self.db, [int(self.task.id)])
        serialized = task_out(self.db, self.task, executions=[], lead_in_map=summary)
        self.assertEqual(serialized.lead_in.state, "added")
        self.assertIsNotNone(serialized.lead_in.last_added_at)

        state = svc.get_task_lead_in_state(self.db, self.task)
        self.assertEqual(state["state"], "added")
        self.assertEqual(state["material_name"], MATERIAL_NAME)

    # ------------------------------------------------------------------
    # 路由直调
    # ------------------------------------------------------------------

    def test_routes_preview_confirm_and_record(self) -> None:
        request = self._request()
        current = self._current_user()
        with patch("app.api.routes.task_lead_in.get_task", return_value=self.task):
            preview = task_lead_in_route.preview_task_lead_in(int(self.task.id), self.db)
            self.assertTrue(preview.available)
            self.assertTrue(preview.preview_token)

            record = task_lead_in_route.get_task_lead_in(int(self.task.id), self.db)
            self.assertEqual(record.state, "none")

            confirmed = task_lead_in_route.post_task_lead_in_confirm(
                int(self.task.id),
                DramaLeadInConfirmIn(preview_token=preview.preview_token),
                request,
                current,
                self.db,
            )
            self.assertEqual(confirmed.status, "added")
            self.assertGreater(confirmed.record_id, 0)

            record_after = task_lead_in_route.get_task_lead_in(int(self.task.id), self.db)
            self.assertEqual(record_after.state, "added")
            self.assertEqual(record_after.material_name, MATERIAL_NAME)

    def test_scan_route_writes_audit(self) -> None:
        result = settings_route.post_drama_lead_in_scan(
            SimpleNamespace(
                drive_type="mockdrive",
                shareurl="https://pan.example.com/s/abc",
                passcode="abcd",
            ),
            self._request("/api/system-settings/drama-lead-in/scan"),
            self._current_user(),
            self.db,
        )
        self.assertEqual(len(result.files), 1)
        self.assertEqual(result.files[0].fid, "mat1")
        audit_rows = (
            self.db.query(AuditLog)
            .filter(AuditLog.action == "system_setting.drama_lead_in_scan")
            .all()
        )
        self.assertEqual(len(audit_rows), 1)


if __name__ == "__main__":
    unittest.main()
