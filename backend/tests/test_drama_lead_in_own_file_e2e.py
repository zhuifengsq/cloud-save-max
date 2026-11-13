"""「本账号文件」素材模式端到端验收测试（契约 docs/追剧任务引流文件-本账号文件模式契约.md §7.3）。

全链路：系统设置（保存 own_file 配置）→ 预览 → 确认 → 任务列表摘要/记录/审计。
全部使用 Mock 适配器 + 内存 sqlite，不发起任何真实网络请求。

说明：本文件是「本账号文件」模式（docs/追剧任务引流文件-本账号文件模式契约.md §7.3）的端到端验收测试，
引用的服务函数 / 请求模型 / 路由路径均已与实现逐字核对（2026-11-09），
若实现侧改名，测试会在收集/断言阶段直接失败，不再保留待核对的占位取名字段。
"""

from __future__ import annotations

import inspect
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from starlette.requests import Request

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
    DramaLeadInConfirmIn,
    DramaLeadInMaterialIn,
    DramaLeadInOwnFileResolveIn,
    DramaLeadInRecordOut,
    DramaLeadInSettingsUpdateIn,
)
import app.application.lead_in.settings as lead_settings
import app.application.lead_in.tokens as lead_tokens
import app.application.lead_in.materials as lead_materials
import app.application.lead_in.records as lead_records
import app.application.use_cases.task_lead_in as lead_usecase
from app.api.presenters.task import task_out
from app.application.queries.task_views import task_view

DRIVE_TYPE = "mockown"
MATERIAL_ACCOUNT = "own-account"
OTHER_ACCOUNT = "own-account-2"
OWN_DIR = "/追剧/本账号测试剧"
OTHER_DIR = "/追剧/本账号测试剧2"

OWN_FILE_PATH = "/素材/引流说明.txt"
OWN_FILE = {
    "fid": "own-fid-引流说明.txt",
    "name": "引流说明.txt",
    "path": OWN_FILE_PATH,
    "size": 1024,
    "md5": "md5-own",
}
SHARE_MATERIAL = {
    "fid": "share-mat-1",
    "name": "分享引流.txt",
    "size": 2048,
    "path": "/分享引流.txt",
}


class MockOwnFileAdapterClass:
    """注册进 ADAPTER_MAP 的测试适配器：同时声明分享 + 本账号文件能力。"""

    DRIVE_TYPE = DRIVE_TYPE
    DRIVE_NAME = "测试网盘"
    SUPPORTS_DRAMA_LEAD_IN = True
    SUPPORTS_LEAD_IN_OWN_FILE = True
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


class MockShareOnlyAdapterClass:
    """声明了引流、但未声明「本账号文件」能力的网盘。"""

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


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/tasks/1/lead-in/confirm",
            "headers": [(b"user-agent", b"pytest")],
            "client": ("127.0.0.1", 12345),
        }
    )


def _current_user() -> SimpleNamespace:
    return SimpleNamespace(user=SimpleNamespace(id=1))


def _routes_by_path(router) -> dict[tuple[str, str], object]:
    routes: dict[tuple[str, str], object] = {}
    for route in router.routes:
        for method in getattr(route, "methods", None) or set():
            routes[(str(route.path), str(method).upper())] = route
    return routes


def _collect_permission_codes(route) -> set[str]:
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


class DramaLeadInOwnFileE2ETests(unittest.TestCase):
    """契约 §7.3：系统设置 → 预览 → 确认全链路 + 摘要/审计/错误码/权限。"""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self._close_db)

        # 注册能力声明用的适配器类；行为由 Mock 实例替代。
        AdapterFactory.register_adapter(DRIVE_TYPE, MockOwnFileAdapterClass)
        self.addCleanup(AdapterFactory.ADAPTER_MAP.pop, DRIVE_TYPE, None)
        AdapterFactory.register_adapter("mockshareonly", MockShareOnlyAdapterClass)
        self.addCleanup(AdapterFactory.ADAPTER_MAP.pop, "mockshareonly", None)
        self.addCleanup(AdapterFactory.clear_cache)

        self.account = DriveAccount(
            name=MATERIAL_ACCOUNT,
            drive_type=DRIVE_TYPE,
            cookie="mock-cookie",
            config_json=json.dumps({"cookie": "mock-cookie"}),
            enabled=True,
            is_default=True,
            runtime_status="active",
        )
        self.other_account = DriveAccount(
            name=OTHER_ACCOUNT,
            drive_type=DRIVE_TYPE,
            cookie="mock-cookie-2",
            config_json=json.dumps({"cookie": "mock-cookie-2"}),
            enabled=True,
            is_default=False,
            runtime_status="active",
        )
        self.task = Task(
            task_uid="t-own-file",
            task_type="drama",
            taskname="本账号文件测试剧",
            shareurl="",
            savepath="追剧/本账号测试剧",
            account_name=MATERIAL_ACCOUNT,
            enabled=True,
        )
        self.other_task = Task(
            task_uid="t-own-file-other",
            task_type="drama",
            taskname="本账号文件测试剧2",
            shareurl="",
            savepath="追剧/本账号测试剧2",
            account_name=OTHER_ACCOUNT,
            enabled=True,
        )
        self.db.add_all([self.account, self.other_account, self.task, self.other_task])
        self.db.commit()

        self.adapter = self._build_mock_adapter()
        self.create_patcher = patch.object(AdapterFactory, "create_adapter", return_value=self.adapter)
        self.create_patcher.start()
        self.addCleanup(self.create_patcher.stop)

    def tearDown(self) -> None:
        try:
            self.db.close()
        finally:
            self.engine.dispose()

    def _close_db(self) -> None:
        try:
            self.db.close()
        finally:
            self.engine.dispose()

    # ------------------------------------------------------------------
    # Mock 适配器 / 设置夹具
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

        def resolve_own_file(file_path):
            path = str(file_path or "")
            name = path.rsplit("/", 1)[-1] or "引流说明.txt"
            return {
                "code": 0,
                "message": "",
                "data": {
                    "file": {
                        "fid": "own-fid-" + name,
                        "name": name,
                        "path": path,
                        "size": 1024,
                        "md5": "md5-own",
                        "dir": False,
                    }
                },
            }

        adapter.lead_in_resolve_dir.side_effect = resolve_dir
        adapter.lead_in_resolve_own_file.side_effect = resolve_own_file
        adapter.lead_in_resolve_material.return_value = {
            "code": 0,
            "message": "",
            "data": {"files": [SHARE_MATERIAL], "material": dict(SHARE_MATERIAL)},
        }
        adapter.lead_in_inspect.return_value = {
            "code": 0,
            "message": "",
            "data": {"state": "absent", "existing": None, "message": ""},
        }
        adapter.lead_in_add.return_value = {"code": 0, "message": "", "data": {"handle": {"id": "share-handle"}}}
        adapter.lead_in_add_own_file.return_value = {
            "code": 0,
            "message": "",
            "data": {"handle": {"task_id": "copy-task-1"}, "sync": False},
        }
        adapter.lead_in_verify.return_value = {
            "code": 0,
            "message": "",
            "data": {"state": "added", "fid": "added-1", "message": "已添加"},
        }
        return adapter

    def _own_file_material_payload(self, **overrides) -> dict:
        payload = {
            "drive_type": DRIVE_TYPE,
            "mode": "own_file",
            "account_name": MATERIAL_ACCOUNT,
            "file_path": OWN_FILE_PATH,
            "file_name": OWN_FILE["name"],
        }
        payload.update(overrides)
        return payload

    def _share_material_payload(self, **overrides) -> dict:
        payload = {
            "drive_type": DRIVE_TYPE,
            "mode": "share",
            "shareurl": "https://mockown.example.com/s/abcdef",
            "passcode": "spass",
            "file_fid": SHARE_MATERIAL["fid"],
            "file_name": SHARE_MATERIAL["name"],
        }
        payload.update(overrides)
        return payload

    def _save_settings(self, materials: list[dict], *, enabled: bool = True):
        payload = DramaLeadInSettingsUpdateIn(
            enabled=enabled,
            materials=[DramaLeadInMaterialIn(**item) for item in materials],
        )
        return settings_route.put_drama_lead_in_settings(payload, _request(), _current_user(), self.db)

    def _configure_own_file(self) -> None:
        self._save_settings([self._own_file_material_payload()])

    def _preview(self, task: Task):
        return task_lead_in_route.preview_task_lead_in(task.id, self.db)

    def _confirm(self, task: Task, token: str):
        return task_lead_in_route.post_task_lead_in_confirm(
            task.id, DramaLeadInConfirmIn(preview_token=token), _request(), _current_user(), self.db
        )

    def _preview_token(self, task: Task) -> str:
        preview = self._preview(task)
        self.assertTrue(preview.available, preview.reason)
        self.assertTrue(preview.preview_token)
        return preview.preview_token

    def _invoke_resolve_endpoint(self, payload_data: dict):
        route = _routes_by_path(settings_route.router).get(("/drama-lead-in/own-file/resolve", "POST"))
        self.assertIsNotNone(
            route,
            "契约 §4.3 要求新增 POST /system-settings/drama-lead-in/own-file/resolve",
        )
        payload = DramaLeadInOwnFileResolveIn(**payload_data)
        args = []
        for name in inspect.signature(route.endpoint).parameters:
            if name in ("payload", "body"):
                args.append(payload)
            elif name == "request":
                args.append(_request())
            elif name == "current":
                args.append(_current_user())
            elif name == "db":
                args.append(self.db)
            else:
                self.fail(f"own-file resolve 路由出现未预期参数: {name}")
        return route.endpoint(*args)

    def _records(self, task: Task | None = None) -> list[TaskLeadInRecord]:
        query = self.db.query(TaskLeadInRecord)
        if task is not None:
            query = query.filter(TaskLeadInRecord.task_id == task.id)
        return query.all()

    def _audit_details(self, action: str) -> list[str]:
        rows = self.db.query(AuditLog).filter(AuditLog.action == action).all()
        return [row.detail or "" for row in rows]

    # ------------------------------------------------------------------
    # 1. 系统设置：保存 / 回显 / 能力校验 / 解析接口
    # ------------------------------------------------------------------

    def test_settings_own_file_save_returns_mode_and_own_file_fields(self) -> None:
        result = self._save_settings([self._own_file_material_payload()])
        dump = result.model_dump()
        self.assertTrue(dump["enabled"])
        drive = next(item for item in dump["drives"] if item["drive_type"] == DRIVE_TYPE)
        self.assertEqual(drive["mode"], "own_file")
        self.assertTrue(drive["own_file_supported"])
        self.assertTrue(drive["configured"])
        self.assertEqual(drive["account_name"], MATERIAL_ACCOUNT)
        self.assertEqual(drive["file_path"], OWN_FILE_PATH)
        self.assertEqual(drive["file_name"], OWN_FILE["name"])
        self.assertEqual(drive["file_size"], OWN_FILE["size"])
        self.assertEqual(drive["config_version"], 1)
        # 设置回显不返回 passcode；shareurl 留空。
        self.assertNotIn("passcode", drive)
        self.assertEqual(drive["shareurl"], "")
        # 保存时使用真实适配器解析了素材文件。
        self.adapter.lead_in_resolve_own_file.assert_called_with(OWN_FILE_PATH)

    def test_settings_own_file_save_requires_adapter_capability(self) -> None:
        payload = [self._own_file_material_payload(drive_type="mockshareonly", account_name="any-account")]
        with self.assertRaises(ApiError) as ctx:
            self._save_settings(payload)
        self.assertEqual(ctx.exception.code, "LEAD_IN_UNSUPPORTED")
        self.assertEqual(ctx.exception.http_status, 400)
        self.assertIn("暂不支持本账号文件引流", ctx.exception.message)
        # 未写库
        self.assertFalse(lead_settings.get_drama_lead_in_materials(self.db))

    def test_settings_own_file_resolve_endpoint_returns_file_info(self) -> None:
        result = self._invoke_resolve_endpoint(
            {"drive_type": DRIVE_TYPE, "account_name": MATERIAL_ACCOUNT, "file_path": OWN_FILE_PATH}
        )
        dump = result.model_dump() if hasattr(result, "model_dump") else dict(result)
        file_info = dump["file"]
        self.assertEqual(file_info["name"], OWN_FILE["name"])
        self.assertEqual(file_info["path"], OWN_FILE_PATH)
        self.assertEqual(file_info["size"], OWN_FILE["size"])
        self.assertFalse(file_info["dir"])
        self.assertEqual(self.adapter.lead_in_resolve_own_file.call_args[0][0], OWN_FILE_PATH)

    def test_settings_own_file_resolve_endpoint_reports_invalid_path(self) -> None:
        # 注意：setUp 已给 lead_in_resolve_own_file 设置 side_effect，其优先级高于
        # return_value，这里必须改用 side_effect 才能覆盖默认的「解析成功」行为。
        self.adapter.lead_in_resolve_own_file.side_effect = [
            {"code": "LEAD_IN_DIR_INVALID", "message": "请填写账号内文件的完整路径"}
        ]
        route_data = {"drive_type": DRIVE_TYPE, "account_name": MATERIAL_ACCOUNT, "file_path": "/素材"}
        with self.assertRaises(ApiError) as ctx:
            self._invoke_resolve_endpoint(route_data)
        self.assertEqual(ctx.exception.code, "LEAD_IN_DIR_INVALID")
        self.assertEqual(ctx.exception.http_status, 400)
        self.assertIn("完整路径", ctx.exception.message)

        self.adapter.lead_in_resolve_own_file.side_effect = [
            {"code": "LEAD_IN_MATERIAL_UNAVAILABLE", "message": "只能选择文件，不能选择文件夹"}
        ]
        with self.assertRaises(ApiError) as ctx:
            self._invoke_resolve_endpoint(route_data)
        self.assertEqual(ctx.exception.code, "LEAD_IN_MATERIAL_UNAVAILABLE")
        self.assertIn("只能选择文件", ctx.exception.message)

    # ------------------------------------------------------------------
    # 2. 预览 → 确认全链路
    # ------------------------------------------------------------------

    def test_own_file_preview_shows_material_source_and_token(self) -> None:
        self._configure_own_file()
        preview = self._preview(self.task)
        self.assertTrue(preview.available, preview.reason)
        self.assertEqual(preview.drive.drive_type, DRIVE_TYPE)
        self.assertEqual(preview.directory.path, OWN_DIR)
        material = preview.material.model_dump()
        self.assertEqual(material["source"], "own_file")
        self.assertEqual(material["name"], OWN_FILE["name"])
        self.assertEqual(material["fid"], "own-fid-引流说明.txt")
        self.assertEqual(material["account_name"], MATERIAL_ACCOUNT)
        self.assertEqual(material["path"], OWN_FILE_PATH)
        # 预览令牌携带素材模式/账号/路径，供确认阶段校验。
        token_payload = lead_tokens.parse_preview_token(preview.preview_token)
        self.assertEqual(token_payload["material_mode"], "own_file")
        self.assertEqual(token_payload["material_account"], MATERIAL_ACCOUNT)
        self.assertEqual(token_payload["material_path"], OWN_FILE_PATH)
        self.assertEqual(token_payload["material_name"], OWN_FILE["name"])

    def test_own_file_confirm_adds_selected_file_to_task_directory(self) -> None:
        self._configure_own_file()
        token = self._preview_token(self.task)
        result = self._confirm(self.task, token)
        dump = result.model_dump()
        self.assertEqual(dump["status"], "added")
        self.assertIsNotNone(dump["added_at"])
        self.assertGreater(dump["record_id"], 0)
        self.assertEqual(dump["directory"]["path"], OWN_DIR)

        # 本账号文件模式必须走内部复制，不能走分享保存接口。
        self.adapter.lead_in_add_own_file.assert_called_once()
        self.adapter.lead_in_add.assert_not_called()
        material_arg, target_arg = self.adapter.lead_in_add_own_file.call_args[0]
        self.assertEqual(material_arg["source"], "own_file")
        self.assertEqual(material_arg["fid"], "own-fid-引流说明.txt")
        self.assertEqual(material_arg["path"], OWN_FILE_PATH)
        self.assertEqual(material_arg["account_name"], MATERIAL_ACCOUNT)
        self.assertEqual(target_arg["dir"]["path"], OWN_DIR)
        self.assertEqual(target_arg["dir"]["fid"], "dir-本账号测试剧")

        # 记录表：唯一一行，status=added，目录为任务 savepath 对应目录。
        records = self._records(self.task)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record.status, "added")
        self.assertEqual(record.drive_type, DRIVE_TYPE)
        self.assertEqual(record.account_name, MATERIAL_ACCOUNT)
        self.assertEqual(record.dir_path, OWN_DIR)
        self.assertEqual(record.material_name, OWN_FILE["name"])

    def test_own_file_task_list_summary_and_record_after_confirm(self) -> None:
        self._configure_own_file()
        token = self._preview_token(self.task)
        self._confirm(self.task, token)

        summary_map = lead_records.load_lead_in_summary_map(self.db, [self.task.id])
        task_dump = task_out(task_view(self.db, self.task, lead_in_map=summary_map)).model_dump()
        self.assertEqual(set(task_dump["lead_in"].keys()), {"state", "last_added_at"})
        self.assertEqual(task_dump["lead_in"]["state"], "added")
        self.assertIsNotNone(task_dump["lead_in"]["last_added_at"])
        self.assertNotIn("shareurl", task_dump["lead_in"])
        self.assertNotIn("passcode", task_dump["lead_in"])
        self.assertNotIn("path", task_dump["lead_in"])

        record = task_lead_in_route.get_task_lead_in(self.task.id, self.db).model_dump()
        self.assertEqual(record["state"], "added")
        self.assertEqual(record["material_name"], OWN_FILE["name"])
        self.assertIsNotNone(record["last_added_at"])
        self.assertEqual(set(record.keys()), set(DramaLeadInRecordOut.model_fields))

    def test_own_file_audit_has_no_sensitive_material_info(self) -> None:
        self._configure_own_file()
        token = self._preview_token(self.task)
        self._confirm(self.task, token)

        details = self._audit_details("task.lead_in_add")
        self.assertEqual(len(details), 1)
        detail = details[0]
        payload = json.loads(detail)
        # 契约 §3.4：审计 detail 保持现状字段，不新增完整文件路径/链接/提取码。
        self.assertEqual(
            set(payload.keys()),
            {"drive_type", "account_name", "dir_path", "material_name", "status", "message"},
        )
        self.assertNotIn("passcode", detail)
        self.assertNotIn("shareurl", detail)
        self.assertNotIn(OWN_FILE_PATH, detail)
        self.assertNotIn("spass", detail)
        # 设置接口审计也不含敏感信息（只有开关与已配置网盘类型）。
        setting_details = self._audit_details("system_setting.drama_lead_in_update")
        self.assertTrue(setting_details)
        for raw in setting_details:
            self.assertNotIn("passcode", raw)
            self.assertNotIn(OWN_FILE_PATH, raw)

    # ------------------------------------------------------------------
    # 3. 账号一致性 / 预览失效
    # ------------------------------------------------------------------

    def test_own_file_account_mismatch_preview_unavailable(self) -> None:
        self._configure_own_file()  # 素材属于 own-account
        self.adapter.lead_in_resolve_own_file.reset_mock()
        preview = self._preview(self.other_task)  # 任务使用的是 own-account-2
        self.assertFalse(preview.available)
        self.assertEqual(
            preview.reason,
            "引流素材属于账号「own-account」，与任务使用的账号「own-account-2」不一致，"
            "无法内部复制；请改用分享链接素材",
        )
        self.assertEqual(preview.preview_token, "")
        # 账号不一致时不应解析素材文件。
        self.adapter.lead_in_resolve_own_file.assert_not_called()

    def test_own_file_stale_preview_after_config_version_change(self) -> None:
        self._configure_own_file()
        token = self._preview_token(self.task)
        # 再次保存同一配置 → config_version +1，旧 token 失效。
        self._save_settings([self._own_file_material_payload()])
        with self.assertRaises(ApiError) as ctx:
            self._confirm(self.task, token)
        self.assertEqual(ctx.exception.code, "LEAD_IN_STALE_PREVIEW")
        self.assertEqual(ctx.exception.http_status, 409)
        self.assertIn("请重新预览", ctx.exception.message)

    def test_own_file_stale_preview_after_file_path_change(self) -> None:
        self._configure_own_file()
        token = self._preview_token(self.task)
        self._save_settings([self._own_file_material_payload(file_path="/素材/新版引流.txt", file_name="新版引流.txt")])
        with self.assertRaises(ApiError) as ctx:
            self._confirm(self.task, token)
        self.assertEqual(ctx.exception.code, "LEAD_IN_STALE_PREVIEW")
        self.assertEqual(ctx.exception.http_status, 409)

    # ------------------------------------------------------------------
    # 4. 查重 / 冲突 / 超时（own_file 模式分派正确）
    # ------------------------------------------------------------------

    def test_own_file_exists_skips_copy(self) -> None:
        self._configure_own_file()
        token = self._preview_token(self.task)
        self.adapter.lead_in_inspect.return_value = {
            "code": 0,
            "message": "目标目录已存在该文件，无需重复添加",
            "data": {"state": "exists", "existing": {"fid": "existing-1"}, "message": ""},
        }
        result = self._confirm(self.task, token)
        dump = result.model_dump()
        self.assertEqual(dump["status"], "exists")
        self.assertIn("已存在", dump["message"])
        self.adapter.lead_in_add_own_file.assert_not_called()
        self.adapter.lead_in_add.assert_not_called()
        self.assertEqual(self._records(self.task)[0].status, "exists")

    def test_own_file_conflict_skips_copy(self) -> None:
        self._configure_own_file()
        token = self._preview_token(self.task)
        self.adapter.lead_in_inspect.return_value = {
            "code": 0,
            "message": "目标目录存在同名文件，未做任何覆盖",
            "data": {"state": "conflict", "existing": {"fid": "conflict-1"}, "message": ""},
        }
        result = self._confirm(self.task, token)
        dump = result.model_dump()
        self.assertEqual(dump["status"], "conflict")
        self.assertIn("同名文件", dump["message"])
        self.adapter.lead_in_add_own_file.assert_not_called()
        self.adapter.lead_in_add.assert_not_called()
        self.assertEqual(self._records(self.task)[0].status, "conflict")

    def test_own_file_timeout_marks_pending(self) -> None:
        self._configure_own_file()
        token = self._preview_token(self.task)
        self.adapter.lead_in_add_own_file.side_effect = TimeoutError("copy timeout")
        result = self._confirm(self.task, token)
        dump = result.model_dump()
        self.assertEqual(dump["status"], "pending")
        self.assertEqual(dump["message"], "网盘响应超时，添加结果待确认")
        record = self._records(self.task)[0]
        self.assertEqual(record.status, "pending")
        self.assertEqual(record.message, "网盘响应超时，添加结果待确认")
        # 超时不算 added，不应写 added_at。
        self.assertIsNone(dump["added_at"])

    # ------------------------------------------------------------------
    # 5. 路由权限 / 既有分享模式回归
    # ------------------------------------------------------------------

    def test_own_file_routes_permission_declarations(self) -> None:
        settings_routes = _routes_by_path(settings_route.router)
        task_routes = _routes_by_path(task_lead_in_route.router)

        def assert_permissions(routes, path: str, method: str, expected: set[str]) -> None:
            route = routes.get((path, method))
            self.assertIsNotNone(route, f"缺少路由 {method} {path}")
            self.assertEqual(_collect_permission_codes(route), expected, f"{method} {path}")

        resolve_path = "/drama-lead-in/own-file/resolve"
        self.assertIn((resolve_path, "POST"), settings_routes, "契约 §4.3 要求新增 own-file resolve 路由")
        assert_permissions(settings_routes, resolve_path, "POST", {TASK_WRITE})
        assert_permissions(settings_routes, "/drama-lead-in", "GET", {TASK_READ})
        assert_permissions(settings_routes, "/drama-lead-in", "PUT", {TASK_WRITE})
        assert_permissions(settings_routes, "/drama-lead-in/scan", "POST", {TASK_WRITE})
        assert_permissions(task_routes, "/{task_id:int}/lead-in/preview", "GET", {TASK_WRITE})
        assert_permissions(task_routes, "/{task_id:int}/lead-in/confirm", "POST", {TASK_WRITE})
        assert_permissions(task_routes, "/{task_id:int}/lead-in", "GET", {TASK_READ})

    def test_share_mode_flow_unchanged_after_own_file_support(self) -> None:
        self._save_settings([self._share_material_payload()])
        preview = self._preview(self.task)
        self.assertTrue(preview.available, preview.reason)
        material = preview.material.model_dump()
        self.assertEqual(material["source"], "share")
        self.assertEqual(material["name"], SHARE_MATERIAL["name"])

        result = self._confirm(self.task, preview.preview_token)
        dump = result.model_dump()
        self.assertEqual(dump["status"], "added")
        self.adapter.lead_in_add.assert_called_once()
        self.adapter.lead_in_add_own_file.assert_not_called()
        self.assertEqual(self.adapter.lead_in_resolve_material.call_args[0][0], "https://mockown.example.com/s/abcdef")


if __name__ == "__main__":
    unittest.main()
