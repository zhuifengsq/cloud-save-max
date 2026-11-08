"""追剧任务引流文件「本账号文件」模式单测。

全部使用 Mock 适配器，不依赖任何真实网盘；同时覆盖 share 模式的零回归。
"""

from __future__ import annotations

import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.api.routes import system_settings_drama_lead_in as settings_route
from app.api.routes import task_lead_in as task_lead_in_route
from app.core.errors import ApiError
from app.core.permissions import TASK_WRITE
from app.db.base import Base
from app.extensions.adapters.adapter_factory import AdapterFactory
from app.models.audit_log import AuditLog
from app.models.drive_account import DriveAccount
from app.models.task import Task
from app.models.task_lead_in import TaskLeadInRecord
from app.schemas.drama_lead_in import DramaLeadInConfirmIn
from app.services import drama_lead_in as svc

DIR_PATH = "/追剧/测试剧"
MATERIAL_NAME = "第01集.mp4"
OWN_FILE_PATH = "/追剧/源/" + MATERIAL_NAME
OWN_FILE_FID = "own1"


class MockOwnFileAdapterClass:
    """声明支持引流且支持本账号文件的测试适配器。"""

    DRIVE_TYPE = "mockown"
    DRIVE_NAME = "测试网盘"
    SUPPORTS_DRAMA_LEAD_IN = True
    SUPPORTS_LEAD_IN_OWN_FILE = True
    SUPPORTS_LEAD_IN_OWN_BROWSE = True
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


class MockShareOnlyAdapterClass:
    """只支持分享链接模式的测试适配器。"""

    DRIVE_TYPE = "mockshareonly"
    DRIVE_NAME = "仅分享网盘"
    SUPPORTS_DRAMA_LEAD_IN = True
    SUPPORTS_LEAD_IN_OWN_FILE = False
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


class MockNoBrowseAdapterClass:
    """支持本账号文件、但未声明目录浏览能力的测试适配器。"""

    DRIVE_TYPE = "mocknobrowse"
    DRIVE_NAME = "无浏览网盘"
    SUPPORTS_DRAMA_LEAD_IN = True
    SUPPORTS_LEAD_IN_OWN_FILE = True
    SUPPORTS_LEAD_IN_OWN_BROWSE = False
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


def _collect_permission_codes(route) -> set[str]:
    """从 require_permissions() 返回闭包的 cell 中取出声明的权限码。"""
    codes: set[str] = set()
    for dependency in getattr(route, "dependencies", None) or []:
        function = getattr(dependency, "dependency", None)
        for cell in getattr(function, "__closure__", None) or ():
            try:
                value = cell.cell_contents
            except ValueError:  # pragma: no cover
                continue
            if isinstance(value, tuple) and value and all(isinstance(item, str) for item in value):
                codes.update(value)
    return codes


class DramaLeadInOwnFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)

        AdapterFactory.register_adapter("mockown", MockOwnFileAdapterClass)
        AdapterFactory.register_adapter("mockshareonly", MockShareOnlyAdapterClass)
        AdapterFactory.register_adapter("mocknobrowse", MockNoBrowseAdapterClass)
        self.addCleanup(AdapterFactory.ADAPTER_MAP.pop, "mockown", None)
        self.addCleanup(AdapterFactory.ADAPTER_MAP.pop, "mockshareonly", None)
        self.addCleanup(AdapterFactory.ADAPTER_MAP.pop, "mocknobrowse", None)

        self.account = DriveAccount(
            name="mock-account",
            drive_type="mockown",
            cookie="cookie-value",
            config_json=json.dumps({"cookie": "cookie-value"}),
            enabled=True,
            is_default=True,
            runtime_status="active",
        )
        self.other_account = DriveAccount(
            name="other-account",
            drive_type="mockown",
            cookie="other-cookie",
            config_json=json.dumps({"cookie": "other-cookie"}),
            enabled=True,
            is_default=False,
            runtime_status="idle",
        )
        self.task = Task(
            task_uid="t-own-1",
            task_type="drama",
            taskname="测试剧",
            shareurl="https://pan.example.com/s/source",
            savepath="追剧/测试剧",
            account_name="mock-account",
            enabled=True,
        )
        self.db.add_all([self.account, self.other_account, self.task])
        self.db.commit()

        self.adapter = Mock()
        self.adapter.ensure_initialized.return_value = None
        self.adapter.lead_in_resolve_dir.return_value = {
            "code": 0,
            "message": "",
            "data": {"dir": {"fid": "dir1", "path": DIR_PATH, "name": "测试剧"}, "entries": []},
        }
        self.adapter.lead_in_resolve_own_file.return_value = {
            "code": 0,
            "message": "",
            "data": {
                "file": {
                    "fid": OWN_FILE_FID,
                    "name": MATERIAL_NAME,
                    "path": OWN_FILE_PATH,
                    "size": 2048,
                    "md5": "md5-own",
                    "dir": False,
                }
            },
        }
        self.adapter.lead_in_list_own_dir.return_value = {
            "code": 0,
            "message": "",
            "data": {
                "path": "/素材",
                "fid": "dir-materials",
                "entries": [
                    {"fid": "dir-1", "name": "子目录", "dir": True, "size": 0, "path": "/素材/子目录"},
                    {
                        "fid": "file-1",
                        "name": MATERIAL_NAME,
                        "dir": False,
                        "size": 1024,
                        "path": "/素材/" + MATERIAL_NAME,
                    },
                ],
            },
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
        self.adapter.lead_in_add.return_value = {"code": 0, "message": "", "data": {"handle": {"id": "h-share"}}}
        self.adapter.lead_in_add_own_file.return_value = {
            "code": 0,
            "message": "",
            "data": {"handle": {"id": "h-own"}, "sync": False},
        }
        self.adapter.lead_in_verify.return_value = {
            "code": 0,
            "message": "",
            "data": {"state": "added", "fid": "file-1", "message": "已添加"},
        }
        self.create_patcher = patch.object(AdapterFactory, "create_adapter", return_value=self.adapter)
        self.create_patcher.start()
        self.addCleanup(self.create_patcher.stop)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _own_file_material(
        self,
        *,
        account_name: str = "mock-account",
        file_path: str = OWN_FILE_PATH,
        config_version: int = 1,
    ) -> dict:
        return {
            "mode": "own_file",
            "account_name": account_name,
            "file_path": file_path,
            "file_fid": OWN_FILE_FID,
            "file_name": MATERIAL_NAME,
            "file_size": 2048,
            "file_md5": "md5-own",
            "config_version": config_version,
            "updated_at": "2026-11-06T20:00:00+08:00",
        }

    def _share_material(self, *, config_version: int = 1) -> dict:
        return {
            "mode": "share",
            "shareurl": "https://pan.example.com/s/abcdef",
            "passcode": "abcd",
            "file_fid": "mat1",
            "file_name": MATERIAL_NAME,
            "config_version": config_version,
            "updated_at": "2026-11-06T20:00:00+08:00",
        }

    def _configure(self, material: dict | None = None) -> None:
        svc._set_setting(self.db, svc.SETTING_KEY_ENABLED, "1", "测试")
        materials = {"mockown": material} if material is not None else {}
        svc._set_setting(
            self.db,
            svc.SETTING_KEY_MATERIALS,
            json.dumps(materials, ensure_ascii=False),
            "测试",
        )
        self.db.commit()

    def _preview(self, task: Task | None = None) -> dict:
        return svc.build_task_lead_in_preview(self.db, task or self.task)

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

    def _record(self) -> TaskLeadInRecord | None:
        return (
            self.db.query(TaskLeadInRecord)
            .filter(TaskLeadInRecord.task_id == int(self.task.id))
            .first()
        )

    # ------------------------------------------------------------------
    # 配置保存：own_file
    # ------------------------------------------------------------------

    def test_save_own_file_persists_fields_and_settings_payload(self) -> None:
        result = svc.update_drama_lead_in_settings(
            self.db,
            {
                "enabled": True,
                "materials": [
                    {
                        "drive_type": "mockown",
                        "mode": "own_file",
                        "account_name": "mock-account",
                        "file_path": OWN_FILE_PATH,
                    }
                ],
            },
        )
        self.adapter.lead_in_resolve_own_file.assert_called_with(OWN_FILE_PATH)

        stored = svc.get_drama_lead_in_materials(self.db)["mockown"]
        self.assertEqual(stored["mode"], "own_file")
        self.assertEqual(stored["account_name"], "mock-account")
        self.assertEqual(stored["file_path"], OWN_FILE_PATH)
        self.assertEqual(stored["file_fid"], OWN_FILE_FID)
        self.assertEqual(stored["file_name"], MATERIAL_NAME)
        self.assertEqual(stored["file_size"], 2048)
        self.assertEqual(stored["file_md5"], "md5-own")
        self.assertEqual(stored["config_version"], 1)

        drive = {item["drive_type"]: item for item in result["drives"]}["mockown"]
        self.assertTrue(drive["configured"])
        self.assertEqual(drive["mode"], "own_file")
        self.assertTrue(drive["own_file_supported"])
        self.assertEqual(drive["account_name"], "mock-account")
        self.assertEqual(drive["file_path"], OWN_FILE_PATH)
        self.assertEqual(drive["file_size"], 2048)
        self.assertEqual(drive["file_name"], MATERIAL_NAME)

    def test_save_own_file_rejects_unknown_account(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            svc.update_drama_lead_in_settings(
                self.db,
                {
                    "enabled": True,
                    "materials": [
                        {
                            "drive_type": "mockown",
                            "mode": "own_file",
                            "account_name": "missing-account",
                            "file_path": OWN_FILE_PATH,
                        }
                    ],
                },
            )
        self.assertEqual(ctx.exception.code, "LEAD_IN_ACCOUNT_UNAVAILABLE")
        self.assertEqual(ctx.exception.http_status, 400)
        self.assertNotIn("mockown", svc.get_drama_lead_in_materials(self.db))

    def test_save_own_file_rejects_invalid_path_without_writing(self) -> None:
        self.adapter.lead_in_resolve_own_file.return_value = {
            "code": "LEAD_IN_DIR_INVALID",
            "message": "请填写账号内文件的完整路径",
            "data": None,
        }
        with self.assertRaises(ApiError) as ctx:
            svc.update_drama_lead_in_settings(
                self.db,
                {
                    "enabled": True,
                    "materials": [
                        {
                            "drive_type": "mockown",
                            "mode": "own_file",
                            "account_name": "mock-account",
                            "file_path": "relative/path.mp4",
                        }
                    ],
                },
            )
        self.assertEqual(ctx.exception.code, "LEAD_IN_DIR_INVALID")
        self.assertEqual(ctx.exception.http_status, 400)
        materials = svc.get_drama_lead_in_materials(self.db)
        self.assertNotIn("mockown", materials)

    def test_save_own_file_with_empty_path_clears_config(self) -> None:
        self._configure(self._own_file_material())
        svc.update_drama_lead_in_settings(
            self.db,
            {
                "enabled": True,
                "materials": [
                    {
                        "drive_type": "mockown",
                        "mode": "own_file",
                        "account_name": "mock-account",
                        "file_path": "",
                    }
                ],
            },
        )
        self.assertNotIn("mockown", svc.get_drama_lead_in_materials(self.db))
        payload = svc.build_settings_payload(self.db)
        drive = {item["drive_type"]: item for item in payload["drives"]}["mockown"]
        self.assertFalse(drive["configured"])
        self.assertEqual(drive["mode"], "share")

    def test_save_own_file_rejects_drive_without_own_file_support(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            svc.update_drama_lead_in_settings(
                self.db,
                {
                    "enabled": True,
                    "materials": [
                        {
                            "drive_type": "mockshareonly",
                            "mode": "own_file",
                            "account_name": "mock-account",
                            "file_path": OWN_FILE_PATH,
                        }
                    ],
                },
            )
        self.assertEqual(ctx.exception.code, "LEAD_IN_UNSUPPORTED")
        self.assertEqual(ctx.exception.http_status, 400)
        self.assertIn("暂不支持本账号文件引流", ctx.exception.message)

    # ------------------------------------------------------------------
    # 本账号文件解析端点
    # ------------------------------------------------------------------

    def test_resolve_own_file_service_and_route(self) -> None:
        result = svc.resolve_drama_lead_in_own_file(self.db, "mockown", "mock-account", OWN_FILE_PATH)
        self.assertEqual(result["file"]["fid"], OWN_FILE_FID)
        self.assertEqual(result["file"]["name"], MATERIAL_NAME)
        self.assertEqual(result["file"]["path"], OWN_FILE_PATH)
        self.assertEqual(result["file"]["size"], 2048)
        self.assertEqual(result["file"]["md5"], "md5-own")

        out = settings_route.post_drama_lead_in_own_file_resolve(
            SimpleNamespace(drive_type="mockown", account_name="mock-account", file_path=OWN_FILE_PATH),
            self._request("/api/system-settings/drama-lead-in/own-file/resolve"),
            self._current_user(),
            self.db,
        )
        self.assertEqual(out.file.fid, OWN_FILE_FID)
        self.assertEqual(out.file.name, MATERIAL_NAME)
        audit_rows = (
            self.db.query(AuditLog)
            .filter(AuditLog.action == "system_setting.drama_lead_in_own_file_resolve")
            .all()
        )
        self.assertEqual(len(audit_rows), 1)
        detail = json.loads(audit_rows[0].detail)
        self.assertEqual(detail["drive_type"], "mockown")
        self.assertEqual(detail["account_name"], "mock-account")
        self.assertNotIn("file_path", detail)

    def test_resolve_own_file_route_surfaces_adapter_error(self) -> None:
        self.adapter.lead_in_resolve_own_file.return_value = {
            "code": "LEAD_IN_MATERIAL_UNAVAILABLE",
            "message": "文件不存在或已被移动",
            "data": None,
        }
        with self.assertRaises(ApiError) as ctx:
            svc.resolve_drama_lead_in_own_file(self.db, "mockown", "mock-account", OWN_FILE_PATH)
        self.assertEqual(ctx.exception.code, "LEAD_IN_MATERIAL_UNAVAILABLE")
        self.assertEqual(ctx.exception.message, "文件不存在或已被移动")

    # ------------------------------------------------------------------
    # 预览：own_file
    # ------------------------------------------------------------------

    def test_preview_own_file_account_mismatch_message(self) -> None:
        self._configure(self._own_file_material(account_name="other-account"))
        preview = self._preview()
        self.assertFalse(preview["available"])
        self.assertEqual(preview["drive"]["drive_type"], "mockown")
        self.assertEqual(
            preview["reason"],
            "引流素材属于账号「other-account」，与任务使用的账号「mock-account」不一致，"
            "无法内部复制；请改用分享链接素材",
        )

    def test_preview_own_file_returns_material_and_token_fields(self) -> None:
        self._configure(self._own_file_material())
        preview = self._preview()
        self.assertTrue(preview["available"], preview.get("reason"))
        self.assertEqual(preview["material"]["source"], "own_file")
        self.assertEqual(preview["material"]["fid"], OWN_FILE_FID)
        self.assertEqual(preview["material"]["name"], MATERIAL_NAME)
        self.assertEqual(preview["material"]["account_name"], "mock-account")
        self.assertEqual(preview["material"]["path"], OWN_FILE_PATH)
        self.assertEqual(preview["state"], "absent")
        self.adapter.lead_in_resolve_own_file.assert_called_with(OWN_FILE_PATH)

        payload = svc.parse_preview_token(preview["preview_token"])
        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["material_mode"], "own_file")
        self.assertEqual(payload["material_account"], "mock-account")
        self.assertEqual(payload["material_path"], OWN_FILE_PATH)
        self.assertEqual(payload["material_name"], MATERIAL_NAME)
        self.assertEqual(payload["material_fid"], OWN_FILE_FID)
        self.assertEqual(payload["material_size"], 2048)
        self.assertEqual(payload["config_version"], 1)
        self.assertGreater(preview["expires_at"].timestamp(), time.time())

        # 设置载荷必须回显 own_file 相关字段，且不返回完整链接/提取码。
        drive = {item["drive_type"]: item for item in svc.build_settings_payload(self.db)["drives"]}["mockown"]
        self.assertTrue(drive["own_file_supported"])
        self.assertEqual(drive["mode"], "own_file")
        self.assertEqual(drive["account_name"], "mock-account")
        self.assertEqual(drive["file_path"], OWN_FILE_PATH)
        self.assertEqual(drive["file_size"], 2048)

    def test_preview_own_file_missing_material_reports_reason(self) -> None:
        self._configure(self._own_file_material())
        self.adapter.lead_in_resolve_own_file.return_value = {
            "code": "LEAD_IN_MATERIAL_UNAVAILABLE",
            "message": "文件不存在或已被移动",
            "data": None,
        }
        preview = self._preview()
        self.assertFalse(preview["available"])
        self.assertEqual(preview["reason"], "文件不存在或已被移动")

    def test_preview_own_file_unsupported_drive(self) -> None:
        self._configure(self._own_file_material())
        task = Task(
            task_uid="t-own-2",
            task_type="drama",
            taskname="仅分享剧",
            shareurl="https://example.com/s/abc",
            savepath="追剧/仅分享",
            account_name=None,
            enabled=True,
        )
        self.db.add(task)
        self.db.commit()
        # 覆盖为仅支持分享的网盘：把任务账号类型切到 mockshareonly。
        account = DriveAccount(
            name="shareonly-account",
            drive_type="mockshareonly",
            cookie="x",
            config_json=json.dumps({"cookie": "x"}),
            enabled=True,
            is_default=True,
            runtime_status="active",
        )
        self.db.add(account)
        task.account_name = "shareonly-account"
        self.db.commit()
        self._configure(self._own_file_material())
        svc._set_setting(
            self.db,
            svc.SETTING_KEY_MATERIALS,
            json.dumps({"mockshareonly": self._own_file_material()}, ensure_ascii=False),
            "测试",
        )
        self.db.commit()
        preview = self._preview(task)
        self.assertFalse(preview["available"])
        self.assertIn("暂不支持本账号文件引流", preview["reason"])

    # ------------------------------------------------------------------
    # 确认：own_file
    # ------------------------------------------------------------------

    def test_confirm_own_file_uses_add_own_file_and_writes_record(self) -> None:
        self._configure(self._own_file_material())
        preview = self._preview()
        token = preview["preview_token"]
        result = svc.confirm_task_lead_in(
            self.db, self.task, token, actor_user_id=1, ip="127.0.0.1", user_agent="test"
        )
        self.assertEqual(result["status"], "added")
        self.assertGreater(result["record_id"], 0)
        self.adapter.lead_in_add_own_file.assert_called_once()
        self.adapter.lead_in_add.assert_not_called()
        self.adapter.lead_in_verify.assert_called_once()

        record = self._record()
        assert record is not None
        self.assertEqual(record.status, "added")
        self.assertEqual(record.material_name, MATERIAL_NAME)
        self.assertEqual(record.material_fid, OWN_FILE_FID)

        audit_rows = self.db.query(AuditLog).filter(AuditLog.action == "task.lead_in_add").all()
        self.assertEqual(len(audit_rows), 1)
        detail = json.loads(audit_rows[0].detail)
        self.assertEqual(detail["status"], "added")
        self.assertNotIn("passcode", detail)
        self.assertNotIn("shareurl", detail)
        self.assertNotIn("file_path", detail)
        self.assertNotIn("material_path", detail)

    def test_confirm_own_file_reports_pending_on_add_timeout(self) -> None:
        self._configure(self._own_file_material())
        token = self._preview()["preview_token"]
        self.adapter.lead_in_add_own_file.side_effect = TimeoutError("timeout")
        result = svc.confirm_task_lead_in(self.db, self.task, token)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["message"], "网盘响应超时，添加结果待确认")
        record = self._record()
        assert record is not None
        self.assertEqual(record.status, "pending")

    def test_confirm_own_file_exists_does_not_copy(self) -> None:
        self._configure(self._own_file_material())
        token = self._preview()["preview_token"]
        self.adapter.lead_in_inspect.return_value = {
            "code": 0,
            "message": "",
            "data": {"state": "exists", "existing": {"fid": "x"}, "message": ""},
        }
        result = svc.confirm_task_lead_in(self.db, self.task, token)
        self.assertEqual(result["status"], "exists")
        self.adapter.lead_in_add_own_file.assert_not_called()
        self.adapter.lead_in_add.assert_not_called()

    def test_confirm_own_file_conflict_does_not_overwrite(self) -> None:
        self._configure(self._own_file_material())
        token = self._preview()["preview_token"]
        self.adapter.lead_in_inspect.return_value = {
            "code": 0,
            "message": "",
            "data": {"state": "conflict", "existing": {"fid": "x"}, "message": "同名文件"},
        }
        result = svc.confirm_task_lead_in(self.db, self.task, token)
        self.assertEqual(result["status"], "conflict")
        self.adapter.lead_in_add_own_file.assert_not_called()
        self.adapter.lead_in_add.assert_not_called()

    def test_confirm_own_file_rejects_stale_path(self) -> None:
        self._configure(self._own_file_material())
        token = self._preview()["preview_token"]
        changed = self._own_file_material(file_path="/追剧/源2/" + MATERIAL_NAME, config_version=2)
        self._configure(changed)
        with self.assertRaises(ApiError) as ctx:
            svc.confirm_task_lead_in(self.db, self.task, token)
        self.assertEqual(ctx.exception.code, "LEAD_IN_STALE_PREVIEW")
        self.assertEqual(ctx.exception.http_status, 409)

    def test_confirm_rejects_mode_change(self) -> None:
        self._configure(self._share_material())
        token = self._preview()["preview_token"]
        self._configure(self._own_file_material())
        with self.assertRaises(ApiError) as ctx:
            svc.confirm_task_lead_in(self.db, self.task, token)
        self.assertEqual(ctx.exception.code, "LEAD_IN_STALE_PREVIEW")
        self.assertEqual(ctx.exception.http_status, 409)

    # ------------------------------------------------------------------
    # share 模式零回归
    # ------------------------------------------------------------------

    def test_share_mode_preview_and_confirm_still_work(self) -> None:
        result = svc.update_drama_lead_in_settings(
            self.db,
            {
                "enabled": True,
                "materials": [
                    {
                        "drive_type": "mockown",
                        "mode": "share",
                        "shareurl": "https://pan.example.com/s/abcdef",
                        "passcode": "abcd",
                        "file_fid": "mat1",
                    }
                ],
            },
        )
        drive = {item["drive_type"]: item for item in result["drives"]}["mockown"]
        self.assertEqual(drive["mode"], "share")
        self.assertEqual(drive["config_version"], 1)
        stored = svc.get_drama_lead_in_materials(self.db)["mockown"]
        self.assertEqual(stored["mode"], "share")
        self.assertEqual(stored["passcode"], "abcd")

        preview = self._preview()
        self.assertTrue(preview["available"], preview.get("reason"))
        self.assertEqual(preview["material"], {"name": MATERIAL_NAME, "fid": "mat1"})
        payload = svc.parse_preview_token(preview["preview_token"])
        assert payload is not None
        self.assertEqual(payload["material_mode"], "share")
        self.assertEqual(payload["material_path"], "")
        self.assertEqual(payload["material_name"], MATERIAL_NAME)

        confirmed = svc.confirm_task_lead_in(self.db, self.task, preview["preview_token"])
        self.assertEqual(confirmed["status"], "added")
        self.adapter.lead_in_add.assert_called_once()
        self.adapter.lead_in_add_own_file.assert_not_called()

    def test_settings_route_own_file_resolve_error_is_api_error(self) -> None:
        self.adapter.lead_in_resolve_own_file.return_value = {
            "code": "LEAD_IN_MATERIAL_UNAVAILABLE",
            "message": "文件不存在或已被移动",
            "data": None,
        }
        with self.assertRaises(ApiError) as ctx:
            settings_route.post_drama_lead_in_own_file_resolve(
                SimpleNamespace(drive_type="mockown", account_name="mock-account", file_path=OWN_FILE_PATH),
                self._request("/api/system-settings/drama-lead-in/own-file/resolve"),
                self._current_user(),
                self.db,
            )
        self.assertEqual(ctx.exception.http_status, 400)
        self.assertEqual(ctx.exception.message, "文件不存在或已被移动")

    def test_task_lead_in_route_preview_and_confirm_with_own_file(self) -> None:
        self._configure(self._own_file_material())
        request = self._request()
        current = self._current_user()
        with patch("app.api.routes.task_lead_in.get_task", return_value=self.task):
            preview = task_lead_in_route.preview_task_lead_in(int(self.task.id), self.db)
            self.assertTrue(preview.available)
            self.assertEqual(preview.material.source, "own_file")
            self.assertEqual(preview.material.account_name, "mock-account")
            self.assertEqual(preview.material.path, OWN_FILE_PATH)

            confirmed = task_lead_in_route.post_task_lead_in_confirm(
                int(self.task.id),
                DramaLeadInConfirmIn(preview_token=preview.preview_token),
                request,
                current,
                self.db,
            )
            self.assertEqual(confirmed.status, "added")
            self.assertEqual(confirmed.material.name, MATERIAL_NAME)

    # ------------------------------------------------------------------
    # 本账号目录浏览（只读）
    # ------------------------------------------------------------------

    def test_list_own_dir_returns_entries(self) -> None:
        result = svc.list_drama_lead_in_own_dir(self.db, "mockown", "mock-account", "/素材")
        self.assertEqual(result["path"], "/素材")
        self.assertEqual(result["fid"], "dir-materials")
        self.assertEqual(len(result["entries"]), 2)
        self.assertTrue(result["entries"][0]["dir"])
        self.assertEqual(result["entries"][0]["size"], 0)
        self.assertEqual(result["entries"][1]["name"], MATERIAL_NAME)
        self.assertEqual(result["entries"][1]["path"], "/素材/" + MATERIAL_NAME)
        self.adapter.lead_in_list_own_dir.assert_called_with("/素材")

    def test_list_own_dir_normalizes_empty_path_to_root(self) -> None:
        self.adapter.lead_in_list_own_dir.return_value = {
            "code": 0,
            "message": "",
            "data": {"path": "/", "fid": "0", "entries": []},
        }
        result = svc.list_drama_lead_in_own_dir(self.db, "mockown", "mock-account", "")
        self.assertEqual(result["path"], "/")
        self.assertEqual(result["fid"], "0")
        self.assertEqual(result["entries"], [])
        self.adapter.lead_in_list_own_dir.assert_called_with("/")

    def test_list_own_dir_rejects_invalid_path(self) -> None:
        for bad_path in ("素材", "../素材", "http://example.com/x"):
            with self.subTest(path=bad_path):
                with self.assertRaises(ApiError) as ctx:
                    svc.list_drama_lead_in_own_dir(self.db, "mockown", "mock-account", bad_path)
                self.assertEqual(ctx.exception.code, "LEAD_IN_DIR_INVALID")
                self.assertEqual(ctx.exception.http_status, 400)
                self.assertTrue(ctx.exception.message)
        self.adapter.lead_in_list_own_dir.assert_not_called()

    def test_list_own_dir_account_unavailable(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            svc.list_drama_lead_in_own_dir(self.db, "mockown", "missing-account", "/")
        self.assertEqual(ctx.exception.code, "LEAD_IN_ACCOUNT_UNAVAILABLE")
        self.assertIn("账号", ctx.exception.message)
        self.adapter.lead_in_list_own_dir.assert_not_called()

    def test_list_own_dir_unsupported_drives(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            svc.list_drama_lead_in_own_dir(self.db, "mocknobrowse", "mock-account", "/")
        self.assertEqual(ctx.exception.code, "LEAD_IN_UNSUPPORTED")
        self.assertIn("暂不支持浏览本账号目录", ctx.exception.message)

        with self.assertRaises(ApiError) as ctx:
            svc.list_drama_lead_in_own_dir(self.db, "unknown-drive", "mock-account", "/")
        self.assertEqual(ctx.exception.code, "LEAD_IN_UNSUPPORTED")
        self.adapter.lead_in_list_own_dir.assert_not_called()

    def test_list_own_dir_passes_adapter_errors(self) -> None:
        for code, message in (
            ("LEAD_IN_DIR_NOT_FOUND", "目录不存在，请重新选择"),
            ("LEAD_IN_DIR_LIST_FAILED", "读取目录失败，请稍后重试"),
        ):
            with self.subTest(code=code):
                self.adapter.lead_in_list_own_dir.return_value = {
                    "code": code,
                    "message": message,
                    "data": None,
                }
                with self.assertRaises(ApiError) as ctx:
                    svc.list_drama_lead_in_own_dir(self.db, "mockown", "mock-account", "/素材")
                self.assertEqual(ctx.exception.code, code)
                self.assertEqual(ctx.exception.message, message)

    def test_list_own_dir_timeout_maps_to_lead_in_timeout(self) -> None:
        self.adapter.lead_in_list_own_dir.side_effect = TimeoutError("read timeout")
        with self.assertRaises(ApiError) as ctx:
            svc.list_drama_lead_in_own_dir(self.db, "mockown", "mock-account", "/")
        self.assertEqual(ctx.exception.code, "LEAD_IN_TIMEOUT")

    def test_list_own_dir_route_writes_redacted_audit(self) -> None:
        out = settings_route.post_drama_lead_in_own_file_list(
            SimpleNamespace(drive_type="mockown", account_name="mock-account", dir_path="/素材"),
            self._request("/api/system-settings/drama-lead-in/own-file/list"),
            self._current_user(),
            self.db,
        )
        self.assertEqual(out.path, "/素材")
        self.assertEqual(out.fid, "dir-materials")
        self.assertEqual(len(out.entries), 2)

        rows = (
            self.db.query(AuditLog)
            .filter(AuditLog.action == "system_setting.drama_lead_in_own_dir_list")
            .all()
        )
        self.assertEqual(len(rows), 1)
        detail = json.loads(rows[0].detail)
        self.assertEqual(set(detail), {"drive_type", "account_name", "entry_count"})
        self.assertEqual(detail["drive_type"], "mockown")
        self.assertEqual(detail["account_name"], "mock-account")
        self.assertEqual(detail["entry_count"], 2)
        self.assertNotIn("/素材", rows[0].detail)
        self.assertNotIn(MATERIAL_NAME, rows[0].detail)

    def test_list_own_dir_route_declares_task_write_permission(self) -> None:
        routes: dict[tuple[str, str], object] = {}
        for route in settings_route.router.routes:
            for method in getattr(route, "methods", None) or set():
                routes[(str(route.path), str(method).upper())] = route
        route = routes[("/drama-lead-in/own-file/list", "POST")]
        self.assertIn(TASK_WRITE, _collect_permission_codes(route))

    def test_settings_payload_exposes_own_file_browse_supported(self) -> None:
        payload = svc.build_settings_payload(self.db)
        drives = {item["drive_type"]: item for item in payload["drives"]}
        self.assertTrue(drives["mockown"]["own_file_browse_supported"])
        self.assertFalse(drives["mockshareonly"]["own_file_browse_supported"])
        self.assertFalse(drives["mocknobrowse"]["own_file_browse_supported"])


if __name__ == "__main__":
    unittest.main()
