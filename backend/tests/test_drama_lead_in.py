"""追剧任务引流文件验收测试（任务书 §16 / 契约 §7）。

全部使用 Mock 适配器 + 内存 sqlite，不发起任何真实网络请求。
覆盖：夸克/百度添加链路、重复点击、同名冲突、并发锁、超时恢复、
手动删除后补回、预览失效、错误原因、权限声明、配置脱敏、可扩展性、配置保存。
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.routes import system_settings_drama_lead_in as settings_route
from app.api.routes import task_lead_in as task_lead_in_route
from app.core.errors import ApiError
from app.core.permissions import TASK_READ, TASK_WRITE
from app.infrastructure.persistence.base import Base
from app.infrastructure.drive.factory import AdapterFactory
from app.infrastructure.persistence.models.audit_log import AuditLog
from app.infrastructure.persistence.models.drive_account import DriveAccount
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.models.task_lead_in import TaskLeadInRecord
from app.schemas.drama_lead_in import (
    DramaLeadInConfirmOut,
    DramaLeadInRecordOut,
    TaskLeadInSummaryOut,
)
import app.application.lead_in.settings as lead_settings
import app.application.lead_in.tokens as lead_tokens
import app.application.lead_in.materials as lead_materials
import app.application.lead_in.records as lead_records
import app.application.use_cases.task_lead_in as lead_usecase
from app.api.presenters.task import task_out
from app.application.queries.task_views import task_view

QUARK_DIR = "/追剧/夸克测试剧"
BAIDU_DIR = "/追剧/百度测试剧"
THIRD_DIR = "/追剧/第三方测试剧"

QUARK_MATERIAL = {"fid": "qmat1", "name": "第01集.mp4", "size": 1024, "path": "/第01集.mp4"}
QUARK_EXTRA = {"fid": "qmat2", "name": "第02集.mp4", "size": 2048, "path": "/第02集.mp4"}
BAIDU_MATERIAL = {"fid": "bmat1", "name": "百度第01集.mp4", "size": 1024, "path": "/百度第01集.mp4"}
BAIDU_EXTRA = {"fid": "bmat2", "name": "百度第02集.mp4", "size": 2048, "path": "/百度第02集.mp4"}
THIRD_MATERIAL = {"fid": "tmat1", "name": "第三方第01集.mp4", "size": 1024, "path": "/第三方第01集.mp4"}
THIRD_EXTRA = {"fid": "tmat2", "name": "第三方第02集.mp4", "size": 2048, "path": "/第三方第02集.mp4"}


class MockThirdAdapterClass:
    """可扩展性验收：临时注册第三种网盘（仅需声明能力 + 注册表元数据）。"""

    DRIVE_TYPE = "mockthird"
    DRIVE_NAME = "第三方网盘"
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

    # 真实实例由 Mock 适配器替代，这里只保留能力声明所需接口形状。
    def ensure_initialized(self, ttl_seconds=None):
        return None

    def lead_in_resolve_dir(self, dir_path):
        raise NotImplementedError

    def lead_in_resolve_material(self, shareurl, passcode, file_fid):
        raise NotImplementedError

    def lead_in_inspect(self, material, target):
        raise NotImplementedError

    def lead_in_add(self, material, target):
        raise NotImplementedError

    def lead_in_verify(self, material, target, handle):
        raise NotImplementedError


class MockDisabledAdapterClass:
    """未声明引流能力的网盘，用于断言 supported=False + 「暂不支持引流」。"""

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


def _collect_permission_codes(route) -> set[str]:
    """从 require_permissions() 返回闭包的 cell 中取出声明的权限码。"""
    codes: set[str] = set()
    for dependency in getattr(route, "dependencies", None) or []:
        function = getattr(dependency, "dependency", None)
        for cell in getattr(function, "__closure__", None) or ():
            try:
                value = cell.cell_contents
            except ValueError:  # pragma: no cover - 空 cell 不会出现在这里
                continue
            if isinstance(value, tuple) and value and all(isinstance(item, str) for item in value):
                codes.update(value)
    return codes


def _routes_by_path(router) -> dict[tuple[str, str], object]:
    routes: dict[tuple[str, str], object] = {}
    for route in router.routes:
        for method in getattr(route, "methods", None) or set():
            routes[(str(route.path), str(method).upper())] = route
    return routes


class DramaLeadInAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self._close_db)

        self.quark_account = DriveAccount(
            name="quark-account",
            drive_type="quark",
            cookie="quark-cookie",
            config_json=json.dumps({"cookie": "quark-cookie"}),
            enabled=True,
            is_default=True,
            runtime_status="active",
        )
        self.baidu_account = DriveAccount(
            name="baidu-account",
            drive_type="baidu",
            cookie="baidu-cookie",
            config_json=json.dumps({"cookie": "baidu-cookie"}),
            enabled=True,
            is_default=True,
            runtime_status="active",
        )
        self.third_account = DriveAccount(
            name="third-account",
            drive_type="mockthird",
            cookie="third-cookie",
            config_json=json.dumps({"cookie": "third-cookie"}),
            enabled=True,
            is_default=True,
            runtime_status="active",
        )
        self.quark_task = Task(
            task_uid="t-quark",
            task_type="drama",
            taskname="夸克测试剧",
            shareurl="https://pan.quark.cn/s/source",
            savepath="追剧/夸克测试剧",
            account_name="quark-account",
            enabled=True,
        )
        self.baidu_task = Task(
            task_uid="t-baidu",
            task_type="drama",
            taskname="百度测试剧",
            shareurl="https://pan.baidu.com/s/1source",
            savepath="追剧/百度测试剧",
            account_name="baidu-account",
            enabled=True,
        )
        self.third_task = Task(
            task_uid="t-third",
            task_type="drama",
            taskname="第三方测试剧",
            shareurl="https://third.example.com/s/t-source",
            savepath="追剧/第三方测试剧",
            account_name="third-account",
            enabled=True,
        )
        self.db.add_all(
            [
                self.quark_account,
                self.baidu_account,
                self.third_account,
                self.quark_task,
                self.baidu_task,
                self.third_task,
            ]
        )
        self.db.commit()

        self.adapter = self._build_mock_adapter()
        self.create_patcher = patch.object(AdapterFactory, "create_adapter", return_value=self.adapter)
        self.create_patcher.start()
        self.addCleanup(self.create_patcher.stop)

        self._configure_materials()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _close_db(self) -> None:
        # addCleanup 兜底：即使 tearDown 前失败也能释放。
        try:
            self.db.close()
        finally:
            self.engine.dispose()

    # ------------------------------------------------------------------
    # 夹具
    # ------------------------------------------------------------------

    def _build_mock_adapter(self) -> Mock:
        adapter = Mock()
        adapter.ensure_initialized.return_value = None

        def resolve_dir(dir_path):
            name = str(dir_path).rsplit("/", 1)[-1] or "根目录"
            return {
                "code": 0,
                "message": "",
                "data": {"dir": {"fid": "dir-" + name, "path": str(dir_path), "name": name}, "entries": []},
            }

        def resolve_material(shareurl, passcode, file_fid):
            url = str(shareurl or "")
            if "baidu" in url:
                material, extra = BAIDU_MATERIAL, BAIDU_EXTRA
            elif "third" in url:
                material, extra = THIRD_MATERIAL, THIRD_EXTRA
            else:
                material, extra = QUARK_MATERIAL, QUARK_EXTRA
            return {"code": 0, "message": "", "data": {"files": [material, extra], "material": material}}

        adapter.lead_in_resolve_dir.side_effect = resolve_dir
        adapter.lead_in_resolve_material.side_effect = resolve_material
        adapter.lead_in_inspect.return_value = {
            "code": 0,
            "message": "",
            "data": {"state": "absent", "existing": None, "message": ""},
        }
        adapter.lead_in_add.return_value = {"code": 0, "message": "", "data": {"handle": {"id": "handle-1"}}}
        adapter.lead_in_verify.return_value = {
            "code": 0,
            "message": "",
            "data": {"state": "added", "fid": "added-1", "message": "已添加"},
        }
        return adapter

    def _material_entry(self, drive_type: str, *, config_version: int = 1) -> dict:
        table = {
            "quark": {
                "shareurl": "https://pan.quark.cn/s/abcdef",
                "passcode": "qpass",
                "file_fid": QUARK_MATERIAL["fid"],
                "file_name": QUARK_MATERIAL["name"],
            },
            "baidu": {
                "shareurl": "https://pan.baidu.com/s/1abcdef",
                "passcode": "bpass",
                "file_fid": BAIDU_MATERIAL["fid"],
                "file_name": BAIDU_MATERIAL["name"],
            },
            "mockthird": {
                "shareurl": "https://third.example.com/s/abcdef",
                "passcode": "tpass",
                "file_fid": THIRD_MATERIAL["fid"],
                "file_name": THIRD_MATERIAL["name"],
            },
        }[drive_type]
        return {
            **table,
            "config_version": config_version,
            "updated_at": "2026-11-06T20:00:00+08:00",
        }

    def _materials(self, *, config_version: int = 1) -> dict:
        return {
            drive_type: self._material_entry(drive_type, config_version=config_version)
            for drive_type in ("quark", "baidu", "mockthird")
        }

    def _write_settings(self, *, enabled: bool = True, materials: dict | None = None) -> None:
        lead_settings._set_setting(self.db, lead_materials.SETTING_KEY_ENABLED, "1" if enabled else "0", "测试")
        payload = self._materials() if materials is None else materials
        lead_settings._set_setting(
            self.db,
            lead_materials.SETTING_KEY_MATERIALS,
            json.dumps(payload, ensure_ascii=False),
            "测试",
        )
        self.db.commit()

    def _configure_materials(self, *, config_version: int = 1) -> None:
        self._write_settings(materials=self._materials(config_version=config_version))

    def _preview(self, task: Task) -> dict:
        return lead_usecase.TaskLeadInUseCase(None)._preview(self.db, task)

    def _preview_token(self, task: Task) -> str:
        preview = self._preview(task)
        self.assertTrue(preview["available"], preview.get("reason"))
        token = preview["preview_token"]
        self.assertTrue(token)
        return token

    def _confirm(self, task: Task, token: str | None = None, **kwargs) -> dict:
        return lead_usecase.TaskLeadInUseCase(None)._confirm(self.db, task, token or self._preview_token(task), **kwargs)

    def _records(self, task: Task | None = None) -> list[TaskLeadInRecord]:
        query = self.db.query(TaskLeadInRecord)
        if task is not None:
            query = query.filter(TaskLeadInRecord.task_id == task.id)
        return query.all()

    # ------------------------------------------------------------------
    # 1. 夸克/百度正常添加链路
    # ------------------------------------------------------------------

    def test_public_usecase_commit_failure_never_replays_external_add(self):
        task_id = self.quark_task.id
        usecase = lead_usecase.TaskLeadInUseCase(self.db)
        preview = usecase.preview(lead_usecase.LeadInCommand(task_id))
        command = lead_usecase.LeadInCommand(task_id, preview.data['preview_token'])
        original_commit = self.db.commit

        def commit():
            if self.adapter.lead_in_add.call_count:
                raise RuntimeError('record commit failed')
            return original_commit()

        original_add = self.adapter.lead_in_add.return_value
        def add(*args):
            self.assertFalse(self.db.in_transaction())
            return original_add

        self.adapter.lead_in_add.side_effect = add
        with patch.object(self.db, 'commit', side_effect=commit):
            with self.assertRaisesRegex(RuntimeError, 'record commit failed'):
                usecase.confirm(command)
        self.adapter.lead_in_add.assert_called_once()
        self.assertEqual(self.db.query(TaskLeadInRecord).count(), 0)

    def test_quark_add_flow_writes_selected_file_to_task_directory(self) -> None:
        preview = self._preview(self.quark_task)
        self.assertTrue(preview["available"], preview.get("reason"))
        self.assertEqual(preview["drive"]["drive_type"], "quark")
        self.assertTrue(preview["drive"]["supported"])
        self.assertEqual(preview["account"]["account_name"], "quark-account")
        self.assertEqual(preview["directory"]["path"], QUARK_DIR)
        self.assertEqual(preview["directory"]["fid"], "dir-夸克测试剧")
        self.assertEqual(preview["material"], {"name": QUARK_MATERIAL["name"], "fid": QUARK_MATERIAL["fid"]})
        self.assertEqual(preview["state"], "absent")
        self.assertIsNone(preview["last_added_at"])
        self.assertIsInstance(preview["expires_at"], datetime)
        self.assertTrue(preview["preview_token"])

        result = lead_usecase.TaskLeadInUseCase(None)._confirm(
            self.db,
            self.quark_task,
            preview["preview_token"],
            actor_user_id=7,
            ip="10.0.0.1",
        )
        self.assertEqual(result["status"], "added")
        # message 取自适配器 verify 返回（真实适配器文案可能不同，只断言语义）
        self.assertIn("添加", result["message"])
        self.assertEqual(result["material"], {"name": QUARK_MATERIAL["name"], "fid": "qmat1"})
        self.assertEqual(result["directory"]["path"], QUARK_DIR)
        self.assertIsNotNone(result["added_at"])
        self.assertGreater(result["record_id"], 0)

        # 只写选中文件 + 只写任务 savepath 对应目录
        self.assertEqual(self.adapter.lead_in_resolve_dir.call_args[0][0], QUARK_DIR)
        self.assertEqual(
            self.adapter.lead_in_resolve_material.call_args[0],
            ("https://pan.quark.cn/s/abcdef", "qpass", "qmat1"),
        )
        self.adapter.lead_in_add.assert_called_once()
        add_material, add_target = self.adapter.lead_in_add.call_args[0]
        self.assertEqual(add_material["fid"], "qmat1")
        self.assertEqual(add_material["name"], QUARK_MATERIAL["name"])
        self.assertNotIn(QUARK_EXTRA["fid"], json.dumps(add_material, ensure_ascii=False))
        self.assertEqual(add_target["dir"]["path"], QUARK_DIR)
        self.adapter.lead_in_verify.assert_called_once()
        self.assertEqual(self.adapter.lead_in_verify.call_args[0][2], {"id": "handle-1"})

        records = self._records(self.quark_task)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.status, "added")
        self.assertEqual(record.drive_type, "quark")
        self.assertEqual(record.account_name, "quark-account")
        self.assertEqual(record.dir_path, QUARK_DIR)
        self.assertEqual(record.material_fid, "qmat1")
        self.assertEqual(record.material_name, QUARK_MATERIAL["name"])
        self.assertEqual(record.config_version, 1)
        # 不得写到其他任务/目录
        self.assertEqual(self._records(self.baidu_task), [])
        self.assertEqual(self._records(self.third_task), [])

        audits = self.db.query(AuditLog).filter(AuditLog.action == "task.lead_in_add").all()
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0].target_type, "task")
        self.assertEqual(audits[0].target_id, str(self.quark_task.id))
        self.assertTrue(audits[0].success)
        detail = json.loads(audits[0].detail)
        self.assertEqual(detail["status"], "added")
        self.assertEqual(detail["drive_type"], "quark")
        self.assertEqual(detail["dir_path"], QUARK_DIR)
        detail_text = json.dumps(detail, ensure_ascii=False)
        for secret in ("qpass", "abcdef", "shareurl", "passcode"):
            self.assertNotIn(secret, detail_text)

    def test_baidu_add_flow_uses_passcode_and_own_directory(self) -> None:
        preview = self._preview(self.baidu_task)
        self.assertTrue(preview["available"], preview.get("reason"))
        self.assertEqual(preview["drive"]["drive_type"], "baidu")
        self.assertEqual(preview["account"]["account_name"], "baidu-account")
        self.assertEqual(preview["directory"]["path"], BAIDU_DIR)
        self.assertEqual(preview["material"], {"name": BAIDU_MATERIAL["name"], "fid": "bmat1"})

        result = lead_usecase.TaskLeadInUseCase(None)._confirm(self.db, self.baidu_task, preview["preview_token"])
        self.assertEqual(result["status"], "added")
        self.assertEqual(result["material"]["fid"], "bmat1")
        self.assertEqual(result["directory"]["path"], BAIDU_DIR)

        self.assertEqual(self.adapter.lead_in_resolve_dir.call_args[0][0], BAIDU_DIR)
        self.assertEqual(
            self.adapter.lead_in_resolve_material.call_args[0],
            ("https://pan.baidu.com/s/1abcdef", "bpass", "bmat1"),
        )
        self.adapter.lead_in_add.assert_called_once()
        add_material, add_target = self.adapter.lead_in_add.call_args[0]
        self.assertEqual(add_material["fid"], "bmat1")
        self.assertNotIn(BAIDU_EXTRA["fid"], json.dumps(add_material, ensure_ascii=False))
        self.assertEqual(add_target["dir"]["path"], BAIDU_DIR)

        records = self._records(self.baidu_task)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "added")
        self.assertEqual(records[0].drive_type, "baidu")
        self.assertEqual(records[0].dir_path, BAIDU_DIR)
        # 夸克任务没有被顺带写入
        self.assertEqual(self._records(self.quark_task), [])
        self.assertEqual(self.db.query(TaskLeadInRecord).count(), 1)

    # ------------------------------------------------------------------
    # 2. 重复点击 / 同名冲突
    # ------------------------------------------------------------------

    def test_repeat_click_on_existing_file_returns_exists_without_add(self) -> None:
        self.adapter.lead_in_inspect.return_value = {
            "code": 0,
            "message": "",
            "data": {"state": "exists", "existing": {"fid": "qmat1"}, "message": "目标目录已存在该文件"},
        }
        preview = self._preview(self.quark_task)
        self.assertEqual(preview["state"], "exists")

        result = lead_usecase.TaskLeadInUseCase(None)._confirm(self.db, self.quark_task, preview["preview_token"])
        self.assertEqual(result["status"], "exists")
        self.assertIn("已存在", result["message"])
        self.assertIsNone(result["added_at"])
        self.adapter.lead_in_add.assert_not_called()
        self.adapter.lead_in_verify.assert_not_called()

        records = self._records(self.quark_task)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "exists")

    def test_click_again_after_added_returns_exists_and_keeps_single_record(self) -> None:
        first = self._confirm(self.quark_task)
        self.assertEqual(first["status"], "added")
        self.adapter.lead_in_inspect.return_value = {
            "code": 0,
            "message": "",
            "data": {"state": "exists", "existing": {"fid": "qmat1"}, "message": "目标目录已存在该文件"},
        }

        preview = self._preview(self.quark_task)
        self.assertEqual(preview["state"], "exists")
        second = lead_usecase.TaskLeadInUseCase(None)._confirm(self.db, self.quark_task, preview["preview_token"])
        self.assertEqual(second["status"], "exists")
        # 重复点击不得重复写入
        self.assertEqual(self.adapter.lead_in_add.call_count, 1)
        records = self._records(self.quark_task)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "exists")

    def test_conflict_does_not_overwrite_or_add(self) -> None:
        self.adapter.lead_in_inspect.return_value = {
            "code": 0,
            "message": "",
            "data": {"state": "conflict", "existing": {"name": "第01集.mp4"}, "message": "目标目录存在同名文件"},
        }
        preview = self._preview(self.quark_task)
        self.assertEqual(preview["state"], "conflict")

        result = lead_usecase.TaskLeadInUseCase(None)._confirm(self.db, self.quark_task, preview["preview_token"])
        self.assertEqual(result["status"], "conflict")
        self.assertIn("同名", result["message"])
        self.adapter.lead_in_add.assert_not_called()
        self.adapter.lead_in_verify.assert_not_called()
        records = self._records(self.quark_task)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "conflict")

    # ------------------------------------------------------------------
    # 3. 并发：锁冲突 409 + 只发生一次 add
    # ------------------------------------------------------------------

    def test_confirm_when_lock_held_returns_in_progress(self) -> None:
        token = self._preview_token(self.quark_task)
        lock = lead_usecase.lead_in_lock("quark", "quark-account", QUARK_DIR, QUARK_MATERIAL["name"])
        self.assertTrue(lock.acquire(blocking=False))
        try:
            with self.assertRaises(ApiError) as ctx:
                lead_usecase.TaskLeadInUseCase(None)._confirm(self.db, self.quark_task, token)
        finally:
            lock.release()

        self.assertEqual(ctx.exception.code, "LEAD_IN_IN_PROGRESS")
        self.assertEqual(ctx.exception.http_status, 409)
        self.assertEqual(ctx.exception.message, "该目录正在添加同名引流文件，请稍后再试")
        self.adapter.lead_in_add.assert_not_called()
        self.assertEqual(self._records(self.quark_task), [])

    def test_concurrent_confirm_same_directory_only_one_add(self) -> None:
        twin = Task(
            task_uid="t-quark-twin",
            task_type="drama",
            taskname="夸克测试剧（同目录）",
            shareurl="https://pan.quark.cn/s/source2",
            savepath="追剧/夸克测试剧",
            account_name="quark-account",
            enabled=True,
        )
        self.db.add(twin)
        self.db.commit()
        token_main = self._preview_token(self.quark_task)
        token_twin = self._preview_token(twin)

        nested_errors: list[ApiError] = []

        def fake_add(material, target):
            # 模拟「写入进行中，另一个任务同目录同名确认」的并发路径。
            if not nested_errors:
                try:
                    lead_usecase.TaskLeadInUseCase(None)._confirm(self.db, twin, token_twin)
                except ApiError as exc:
                    nested_errors.append(exc)
            return {"code": 0, "message": "", "data": {"handle": {"id": "handle-1"}}}

        self.adapter.lead_in_add.side_effect = fake_add
        result = lead_usecase.TaskLeadInUseCase(None)._confirm(self.db, self.quark_task, token_main)

        self.assertEqual(result["status"], "added")
        self.assertEqual(len(nested_errors), 1)
        self.assertEqual(nested_errors[0].code, "LEAD_IN_IN_PROGRESS")
        self.assertEqual(nested_errors[0].http_status, 409)
        self.assertEqual(self.adapter.lead_in_add.call_count, 1)
        self.assertEqual(len(self._records(self.quark_task)), 1)
        self.assertEqual(self._records(twin), [])

    # ------------------------------------------------------------------
    # 4. 超时恢复 / 删除后补回
    # ------------------------------------------------------------------

    def test_add_timeout_stores_pending_then_retry_adds_after_absent(self) -> None:
        self.adapter.lead_in_add.side_effect = [
            TimeoutError("read timeout"),
            {"code": 0, "message": "", "data": {"handle": {"id": "handle-2"}}},
        ]

        first = self._confirm(self.quark_task)
        self.assertEqual(first["status"], "pending")
        self.assertEqual(first["message"], "网盘响应超时，添加结果待确认")
        self.assertIsNone(first["added_at"])
        records = self._records(self.quark_task)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "pending")
        self.assertEqual(records[0].message, "网盘响应超时，添加结果待确认")
        self.assertEqual(self.adapter.lead_in_add.call_count, 1)

        # 再次操作：预览查重 absent（未写入）→ 允许重新添加
        preview = self._preview(self.quark_task)
        self.assertEqual(preview["state"], "absent")
        second = lead_usecase.TaskLeadInUseCase(None)._confirm(self.db, self.quark_task, preview["preview_token"])
        self.assertEqual(second["status"], "added")
        self.assertEqual(self.adapter.lead_in_add.call_count, 2)
        records = self._records(self.quark_task)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "added")

    def test_pending_then_inspect_exists_does_not_resend_add(self) -> None:
        self.adapter.lead_in_add.side_effect = [TimeoutError("timeout")]
        first = self._confirm(self.quark_task)
        self.assertEqual(first["status"], "pending")
        self.assertEqual(self.adapter.lead_in_add.call_count, 1)

        # 第二次操作先查重：目标已存在 → exists，不盲目重发
        self.adapter.lead_in_inspect.return_value = {
            "code": 0,
            "message": "",
            "data": {"state": "exists", "existing": {"fid": "qmat1"}, "message": "目标目录已存在该文件"},
        }
        second = self._confirm(self.quark_task)
        self.assertEqual(second["status"], "exists")
        self.assertEqual(self.adapter.lead_in_add.call_count, 1)

    def test_manual_delete_clears_record_then_add_again(self) -> None:
        first = self._confirm(self.quark_task)
        self.assertEqual(first["status"], "added")
        self.assertEqual(self.adapter.lead_in_add.call_count, 1)

        # 用户在网盘手动删除该文件 → 查重回到 absent：过期记录被清掉，
        # 任务列表的「引流」列回到「—」，最近添加时间一并消失。
        self.adapter.lead_in_inspect.return_value = {
            "code": 0,
            "message": "",
            "data": {"state": "absent", "existing": None, "message": ""},
        }
        preview = self._preview(self.quark_task)
        self.assertEqual(preview["state"], "absent")
        self.assertIsNone(preview["last_added_at"])
        self.assertEqual(self._records(self.quark_task), [])
        summary_map = lead_records.load_lead_in_summary_map(self.db, [self.quark_task.id])
        self.assertEqual(summary_map[self.quark_task.id]["state"], "none")
        self.assertIsNone(summary_map[self.quark_task.id]["last_added_at"])

        second = lead_usecase.TaskLeadInUseCase(None)._confirm(self.db, self.quark_task, preview["preview_token"])
        self.assertEqual(second["status"], "added")
        self.assertEqual(self.adapter.lead_in_add.call_count, 2)
        records = self._records(self.quark_task)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "added")

    # ------------------------------------------------------------------
    # 5. 预览失效（409 LEAD_IN_STALE_PREVIEW）
    # ------------------------------------------------------------------

    def test_stale_preview_after_material_config_version_change(self) -> None:
        token = self._preview_token(self.quark_task)
        self._configure_materials(config_version=2)
        with self.assertRaises(ApiError) as ctx:
            lead_usecase.TaskLeadInUseCase(None)._confirm(self.db, self.quark_task, token)

        self.assertEqual(ctx.exception.code, "LEAD_IN_STALE_PREVIEW")
        self.assertEqual(ctx.exception.http_status, 409)
        self.assertEqual(ctx.exception.message, "预览内容已变化，请重新预览")
        self.adapter.lead_in_add.assert_not_called()
        self.assertEqual(self._records(self.quark_task), [])

    def test_stale_preview_after_same_drive_account_change(self):
        task_id = self.quark_task.id
        usecase = lead_usecase.TaskLeadInUseCase(self.db)
        token = usecase.preview(lead_usecase.LeadInCommand(task_id)).data['preview_token']
        self.db.add(DriveAccount(name='another-quark', drive_type='quark', cookie='private', enabled=True))
        self.quark_task.account_name = 'another-quark'
        self.db.commit()
        with self.assertRaises(ApiError) as caught:
            usecase.confirm(lead_usecase.LeadInCommand(task_id, token))
        self.assertEqual(caught.exception.code, 'LEAD_IN_STALE_PREVIEW')
        self.adapter.lead_in_add.assert_not_called()
        self.assertEqual(self._records(self.quark_task), [])

    def test_stale_preview_after_default_account_change(self):
        self.quark_task.account_name = None
        self.db.commit()
        task_id = self.quark_task.id
        usecase = lead_usecase.TaskLeadInUseCase(self.db)
        token = usecase.preview(lead_usecase.LeadInCommand(task_id)).data['preview_token']
        self.quark_account.is_default = False
        self.db.add(DriveAccount(name='new-default-quark', drive_type='quark', cookie='private', enabled=True, is_default=True, runtime_status='active'))
        self.db.commit()
        with self.assertRaises(ApiError) as caught:
            usecase.confirm(lead_usecase.LeadInCommand(task_id, token))
        self.assertEqual(caught.exception.code, 'LEAD_IN_STALE_PREVIEW')
        self.adapter.lead_in_add.assert_not_called()

    def test_stale_preview_after_task_savepath_change(self) -> None:
        token = self._preview_token(self.quark_task)
        self.quark_task.savepath = "追剧/换了的目录"
        self.db.commit()
        with self.assertRaises(ApiError) as ctx:
            lead_usecase.TaskLeadInUseCase(None)._confirm(self.db, self.quark_task, token)

        self.assertEqual(ctx.exception.code, "LEAD_IN_STALE_PREVIEW")
        self.assertEqual(ctx.exception.http_status, 409)
        self.adapter.lead_in_add.assert_not_called()
        self.assertEqual(self._records(self.quark_task), [])

    # ------------------------------------------------------------------
    # 6. 错误原因（available=False + 中文 reason）
    # ------------------------------------------------------------------

    def test_preview_switch_off_reason(self) -> None:
        self._write_settings(enabled=False, materials=self._materials())
        preview = self._preview(self.quark_task)
        self.assertFalse(preview["available"])
        self.assertEqual(preview["reason"], "引流功能已关闭")
        self.assertEqual(preview["preview_token"], "")

    def test_preview_material_not_configured_reason(self) -> None:
        materials = self._materials()
        materials.pop("quark")
        self._write_settings(materials=materials)
        preview = self._preview(self.quark_task)
        self.assertFalse(preview["available"])
        self.assertEqual(preview["reason"], "该网盘未配置引流素材")
        self.assertEqual(preview["preview_token"], "")

    def test_preview_account_unavailable_reason(self) -> None:
        self.quark_account.enabled = False
        self.db.commit()
        preview = self._preview(self.quark_task)
        self.assertFalse(preview["available"])
        self.assertEqual(preview["reason"], "网盘账号不可用")
        self.assertEqual(preview["preview_token"], "")

    def test_preview_directory_missing_reason(self) -> None:
        self.adapter.lead_in_resolve_dir.side_effect = None
        self.adapter.lead_in_resolve_dir.return_value = {
            "code": "LEAD_IN_DIR_NOT_FOUND",
            "message": "目录不存在",
            "data": None,
        }
        preview = self._preview(self.quark_task)
        self.assertFalse(preview["available"])
        self.assertEqual(preview["reason"], "任务保存目录不存在")
        self.assertEqual(preview["preview_token"], "")

    def test_preview_unsupported_drive_reason(self) -> None:
        xunlei_task = Task(
            task_uid="t-xunlei",
            task_type="drama",
            taskname="迅雷测试剧",
            shareurl="https://pan.xunlei.com/s/xyz",
            savepath="追剧/迅雷测试剧",
            account_name=None,
            enabled=True,
        )
        self.db.add(xunlei_task)
        self.db.commit()
        preview = self._preview(xunlei_task)
        self.assertFalse(preview["available"])
        self.assertEqual(preview["reason"], "该网盘暂不支持引流")
        self.assertEqual(preview["drive"]["drive_type"], "xunlei")
        self.assertFalse(preview["drive"]["supported"])

    # ------------------------------------------------------------------
    # 7. 路由权限声明
    # ------------------------------------------------------------------

    def test_lead_in_routes_declare_task_permissions(self) -> None:
        task_routes = _routes_by_path(task_lead_in_route.router)
        self.assertIn(TASK_WRITE, _collect_permission_codes(task_routes[("/{task_id:int}/lead-in/preview", "GET")]))
        self.assertIn(TASK_WRITE, _collect_permission_codes(task_routes[("/{task_id:int}/lead-in/confirm", "POST")]))
        self.assertIn(TASK_READ, _collect_permission_codes(task_routes[("/{task_id:int}/lead-in", "GET")]))

        settings_routes = _routes_by_path(settings_route.router)
        self.assertIn(TASK_READ, _collect_permission_codes(settings_routes[("/drama-lead-in", "GET")]))
        self.assertIn(TASK_WRITE, _collect_permission_codes(settings_routes[("/drama-lead-in", "PUT")]))
        self.assertIn(TASK_WRITE, _collect_permission_codes(settings_routes[("/drama-lead-in/scan", "POST")]))

    # ------------------------------------------------------------------
    # 8. 配置安全：脱敏 + 不泄漏
    # ------------------------------------------------------------------

    def test_settings_payload_masks_link_and_hides_passcode(self) -> None:
        payload = lead_settings.build_settings_payload(self.db)
        drives = {item["drive_type"]: item for item in payload["drives"]}
        quark = drives["quark"]
        self.assertTrue(quark["supported"])
        self.assertTrue(quark["configured"])
        self.assertTrue(quark["shareurl"].endswith("***"))
        self.assertEqual(quark["shareurl"], "https://pan.quark.cn/s/abc***")
        self.assertTrue(quark["passcode_set"])
        self.assertNotIn("passcode", quark)

        text = json.dumps(payload, ensure_ascii=False, default=str)
        for secret in ('"passcode"', "qpass", "bpass", "tpass", "abcdef", "1abcdef"):
            self.assertNotIn(secret, text)

    def test_task_and_record_outputs_expose_only_summary(self) -> None:
        confirm_result = self._confirm(self.quark_task)
        self.assertEqual(confirm_result["status"], "added")

        summary_map = lead_records.load_lead_in_summary_map(self.db, [self.quark_task.id])
        task_payload = task_out(task_view(self.db, self.quark_task, lead_in_map=summary_map))
        dumped = task_payload.model_dump()
        lead_in = dumped["lead_in"]
        self.assertEqual(set(lead_in), {"state", "last_added_at"})
        self.assertEqual(lead_in["state"], "added")
        self.assertIsNotNone(lead_in["last_added_at"])
        lead_in_text = json.dumps(lead_in, ensure_ascii=False, default=str)
        for secret in ("qpass", "abcdef", "shareurl", "passcode"):
            self.assertNotIn(secret, lead_in_text)
        self.assertEqual(set(TaskLeadInSummaryOut(**lead_in).model_dump()), {"state", "last_added_at"})

        record_state = lead_records.get_task_lead_in_state(self.db, self.quark_task)
        self.assertEqual(
            set(DramaLeadInRecordOut(**record_state).model_dump()),
            {"state", "message", "last_added_at", "material_name"},
        )
        self.assertEqual(
            set(DramaLeadInConfirmOut(**confirm_result).model_dump()),
            {"status", "message", "added_at", "record_id", "material", "directory"},
        )

    # ------------------------------------------------------------------
    # 9. 可扩展性：临时注册第三种网盘
    # ------------------------------------------------------------------

    def test_third_adapter_auto_registers_in_capabilities_and_flow(self) -> None:
        # 注册前：第三方任务因能力未声明而不可用
        before = self._preview(self.third_task)
        self.assertFalse(before["available"])
        self.assertEqual(before["reason"], "该网盘暂不支持引流")

        AdapterFactory.register_adapter("mockthird", MockThirdAdapterClass)
        AdapterFactory.register_adapter("mockdisabled", MockDisabledAdapterClass)
        try:
            capabilities = {item["drive_type"]: item for item in lead_materials.list_drive_capabilities()}
            self.assertTrue(capabilities["mockthird"]["supported"])
            self.assertEqual(capabilities["mockthird"]["drive_name"], "第三方网盘")
            self.assertFalse(capabilities["mockdisabled"]["supported"])
            self.assertIn("暂不支持引流", capabilities["mockdisabled"]["reason"])

            drives = {item["drive_type"]: item for item in lead_settings.build_settings_payload(self.db)["drives"]}
            self.assertIn("mockthird", drives)
            self.assertTrue(drives["mockthird"]["supported"])
            self.assertFalse(drives["mockdisabled"]["supported"])

            preview = self._preview(self.third_task)
            self.assertTrue(preview["available"], preview.get("reason"))
            result = lead_usecase.TaskLeadInUseCase(None)._confirm(self.db, self.third_task, preview["preview_token"])
            self.assertEqual(result["status"], "added")
            self.assertEqual(self.adapter.lead_in_resolve_dir.call_args[0][0], THIRD_DIR)
            self.assertEqual(self.adapter.lead_in_add.call_args[0][0]["fid"], "tmat1")
            records = self._records(self.third_task)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].drive_type, "mockthird")
        finally:
            AdapterFactory.ADAPTER_MAP.pop("mockthird", None)
            AdapterFactory.ADAPTER_MAP.pop("mockdisabled", None)

        # 恢复注册表后能力清单同步消失，证明完全由注册表驱动
        self.assertNotIn("mockthird", {item["drive_type"] for item in lead_materials.list_drive_capabilities()})

    # ------------------------------------------------------------------
    # 10. 配置保存
    # ------------------------------------------------------------------

    def test_update_settings_toggle_only_keeps_materials(self) -> None:
        before = lead_settings.get_drama_lead_in_materials(self.db)
        result = lead_settings.update_drama_lead_in_settings(self.db, {"enabled": False, "materials": []})

        self.assertFalse(result["enabled"])
        self.assertFalse(lead_settings.get_drama_lead_in_enabled(self.db))
        self.assertEqual(lead_settings.get_drama_lead_in_materials(self.db), before)
        self.assertEqual(before["quark"]["config_version"], 1)
        self.adapter.lead_in_resolve_material.assert_not_called()

    def test_update_settings_blank_passcode_keeps_original(self) -> None:
        result = lead_settings.update_drama_lead_in_settings(
            self.db,
            {
                "enabled": True,
                "materials": [
                    {
                        "drive_type": "quark",
                        "shareurl": "https://pan.quark.cn/s/newurl",
                        "passcode": "",
                        "file_fid": "qmat1",
                        "file_name": "",
                    }
                ],
            },
        )
        self.assertEqual(
            self.adapter.lead_in_resolve_material.call_args[0],
            ("https://pan.quark.cn/s/newurl", "qpass", "qmat1"),
        )
        stored = lead_settings.get_drama_lead_in_materials(self.db)
        self.assertEqual(stored["quark"]["passcode"], "qpass")
        self.assertEqual(stored["quark"]["config_version"], 2)
        self.assertEqual(stored["quark"]["shareurl"], "https://pan.quark.cn/s/newurl")
        # 未提交的网盘配置保持原样
        self.assertEqual(stored["baidu"]["config_version"], 1)
        self.assertEqual(stored["baidu"]["passcode"], "bpass")
        drives = {item["drive_type"]: item for item in result["drives"]}
        self.assertEqual(drives["quark"]["config_version"], 2)

    def test_update_settings_invalid_link_keeps_db(self) -> None:
        before_materials = lead_settings.get_drama_lead_in_materials(self.db)
        self.assertTrue(lead_settings.get_drama_lead_in_enabled(self.db))
        self.adapter.lead_in_resolve_material.side_effect = None
        self.adapter.lead_in_resolve_material.return_value = {
            "code": "LEAD_IN_MATERIAL_UNAVAILABLE",
            "message": "分享链接已失效，请检查链接与提取码",
            "data": None,
        }
        with self.assertRaises(ApiError) as ctx:
            lead_settings.update_drama_lead_in_settings(
                self.db,
                {
                    "enabled": False,
                    "materials": [
                        {
                            "drive_type": "quark",
                            "shareurl": "https://pan.quark.cn/s/badlink",
                            "passcode": "",
                            "file_fid": "",
                            "file_name": "",
                        }
                    ],
                },
            )

        self.assertEqual(ctx.exception.code, "LEAD_IN_MATERIAL_UNAVAILABLE")
        self.assertEqual(ctx.exception.http_status, 400)
        self.assertIn("链接", ctx.exception.message)
        self.assertEqual(lead_settings.get_drama_lead_in_materials(self.db), before_materials)
        self.assertTrue(lead_settings.get_drama_lead_in_enabled(self.db))

    # ------------------------------------------------------------------
    # 11. 失败不得误报 added
    # ------------------------------------------------------------------

    def test_confirm_directory_missing_marks_failed_not_added(self) -> None:
        token = self._preview_token(self.quark_task)
        self.adapter.lead_in_resolve_dir.side_effect = None
        self.adapter.lead_in_resolve_dir.return_value = {
            "code": "LEAD_IN_DIR_NOT_FOUND",
            "message": "目录不存在",
            "data": None,
        }
        result = lead_usecase.TaskLeadInUseCase(None)._confirm(self.db, self.quark_task, token)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["message"], "任务保存目录不存在")
        self.assertIsNone(result["added_at"])
        self.adapter.lead_in_add.assert_not_called()
        records = self._records(self.quark_task)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "failed")

    def test_confirm_account_init_failure_marks_failed(self) -> None:
        token = self._preview_token(self.quark_task)
        self.adapter.ensure_initialized.side_effect = RuntimeError("login failed")
        result = lead_usecase.TaskLeadInUseCase(None)._confirm(self.db, self.quark_task, token)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["message"], "网盘账号不可用")
        self.assertIsNone(result["added_at"])
        self.adapter.lead_in_add.assert_not_called()
        records = self._records(self.quark_task)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].status, "failed")


if __name__ == "__main__":
    unittest.main()
