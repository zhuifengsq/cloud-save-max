"""「本账号文件目录浏览」端到端验收测试（契约 docs/追剧任务引流文件-目录浏览契约.md §7）。

覆盖：接口成功（根目录/子目录）、选中文件回填并通过 resolve 保存配置、
账号不可用、目录不存在、目录读取失败、权限不足、路径非法、未支持、
审计脱敏、只读性（不写库/不产生任务/不触发转存）。

全部使用 Mock 适配器 + 内存 sqlite，不发起任何真实网络请求。
实现合并后若命名/签名与契约不一致，先按契约断言，差异作为缺陷回报 Lead。
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
from app.core.errors import ApiError
from app.core.permissions import TASK_WRITE
from app.db.base import Base
from app.extensions.adapters.adapter_factory import AdapterFactory
from app.models.audit_log import AuditLog
from app.models.drive_account import DriveAccount
from app.models.task import Task
from app.models.task_lead_in import TaskLeadInRecord
from app.schemas import drama_lead_in as lead_in_schemas
from app.schemas.drama_lead_in import (
    DramaLeadInMaterialIn,
    DramaLeadInSettingsUpdateIn,
)
from app.services import drama_lead_in as svc

DRIVE_TYPE = "mockbrowse"
NO_BROWSE_DRIVE_TYPE = "mocknobrowse"
ACCOUNT = "browse-account"
DISABLED_ACCOUNT = "browse-account-disabled"
EXISTING_MATERIAL_PATH = "/素材/引流说明.txt"
BROWSE_DIR = "/素材"

ROOT_ENTRIES = [
    {"fid": "dir-素材", "name": "素材", "dir": True, "size": 0, "path": "/素材"},
    {"fid": "file-root", "name": "根目录说明.txt", "dir": False, "size": 100, "path": "/根目录说明.txt"},
]
SUBDIR_ENTRIES = [
    {"fid": "file-引流", "name": "引流说明.txt", "dir": False, "size": 1024, "path": "/素材/引流说明.txt"},
]


class MockBrowseAdapterClass:
    """注册进 ADAPTER_MAP 的测试适配器：声明引流 + 本账号文件 + 目录浏览能力。"""

    DRIVE_TYPE = DRIVE_TYPE
    DRIVE_NAME = "浏览测试网盘"
    SUPPORTS_DRAMA_LEAD_IN = True
    SUPPORTS_LEAD_IN_OWN_FILE = True
    SUPPORTS_LEAD_IN_OWN_BROWSE = True
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
    """声明引流 + 本账号文件，但未声明目录浏览能力。"""

    DRIVE_TYPE = NO_BROWSE_DRIVE_TYPE
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


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/system-settings/drama-lead-in/own-file/list",
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


def _dump(value) -> dict:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return dict(value)


class DramaLeadInOwnDirE2ETests(unittest.TestCase):
    """契约 §7：目录浏览接口、错误原因、审计脱敏、只读性与能力清单。"""

    def setUp(self) -> None:
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.addCleanup(self._close_db)

        AdapterFactory.register_adapter(DRIVE_TYPE, MockBrowseAdapterClass)
        self.addCleanup(AdapterFactory.ADAPTER_MAP.pop, DRIVE_TYPE, None)
        AdapterFactory.register_adapter(NO_BROWSE_DRIVE_TYPE, MockNoBrowseAdapterClass)
        self.addCleanup(AdapterFactory.ADAPTER_MAP.pop, NO_BROWSE_DRIVE_TYPE, None)
        self.addCleanup(AdapterFactory.clear_cache)

        self.account = DriveAccount(
            name=ACCOUNT,
            drive_type=DRIVE_TYPE,
            cookie="mock-cookie",
            config_json=json.dumps({"cookie": "mock-cookie"}),
            enabled=True,
            is_default=True,
            runtime_status="active",
        )
        self.disabled_account = DriveAccount(
            name=DISABLED_ACCOUNT,
            drive_type=DRIVE_TYPE,
            cookie="mock-cookie-disabled",
            config_json=json.dumps({"cookie": "mock-cookie-disabled"}),
            enabled=False,
            is_default=False,
            runtime_status="disabled",
        )
        self.task = Task(
            task_uid="t-own-dir",
            task_type="drama",
            taskname="目录浏览测试剧",
            shareurl="",
            savepath="追剧/目录浏览测试剧",
            account_name=ACCOUNT,
            enabled=True,
        )
        self.db.add_all([self.account, self.disabled_account, self.task])
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
    # Mock 适配器 / helper
    # ------------------------------------------------------------------

    def _build_mock_adapter(self) -> Mock:
        adapter = Mock()
        adapter.ensure_initialized.return_value = None

        def list_own_dir(dir_path):
            path = str(dir_path or "/")
            if path in ("", "/"):
                return {
                    "code": 0,
                    "message": "",
                    "data": {"path": "/", "fid": "0", "entries": [dict(item) for item in ROOT_ENTRIES]},
                }
            if path == BROWSE_DIR:
                return {
                    "code": 0,
                    "message": "",
                    "data": {"path": BROWSE_DIR, "fid": "dir-素材", "entries": [dict(item) for item in SUBDIR_ENTRIES]},
                }
            if path == "/不存在":
                return {"code": "LEAD_IN_DIR_NOT_FOUND", "message": "目录不存在，请重新选择", "data": None}
            if path == "/读取失败":
                return {"code": "LEAD_IN_DIR_LIST_FAILED", "message": "读取目录失败，请稍后重试", "data": None}
            if path == "/无权限":
                return {
                    "code": "LEAD_IN_ACCOUNT_UNAVAILABLE",
                    "message": "网盘账号权限不足或登录已失效，请重新登录",
                    "data": None,
                }
            if path == "/超时":
                raise TimeoutError("read timeout")
            if "://" in path or not path.startswith("/") or "//" in path:
                return {"code": "LEAD_IN_DIR_INVALID", "message": "目录路径不合法", "data": None}
            return {"code": "LEAD_IN_DIR_NOT_FOUND", "message": "目录不存在，请重新选择", "data": None}

        def resolve_own_file(file_path):
            path = str(file_path or "")
            name = path.rsplit("/", 1)[-1]
            return {
                "code": 0,
                "message": "",
                "data": {
                    "file": {
                        "fid": "file-" + name,
                        "name": name,
                        "path": path,
                        "size": 1024,
                        "md5": "md5-own",
                        "dir": False,
                    }
                },
            }

        adapter.lead_in_list_own_dir.side_effect = list_own_dir
        adapter.lead_in_resolve_own_file.side_effect = resolve_own_file
        adapter.lead_in_resolve_dir.return_value = {
            "code": 0,
            "message": "",
            "data": {"dir": {"fid": "dir-task", "path": "/追剧/目录浏览测试剧", "name": "目录浏览测试剧"}, "entries": []},
        }
        adapter.lead_in_inspect.return_value = {
            "code": 0,
            "message": "",
            "data": {"state": "absent", "existing": None, "message": ""},
        }
        adapter.lead_in_add.return_value = {"code": 0, "message": "", "data": {"handle": {"id": "h1"}}}
        adapter.lead_in_add_own_file.return_value = {
            "code": 0,
            "message": "",
            "data": {"handle": {"task_id": "copy-1"}, "sync": False},
        }
        adapter.lead_in_verify.return_value = {
            "code": 0,
            "message": "",
            "data": {"state": "added", "fid": "added-1", "message": "已添加"},
        }
        return adapter

    def _list_dir(self, dir_path: str, *, drive_type: str = DRIVE_TYPE, account_name: str = ACCOUNT):
        return svc.list_drama_lead_in_own_dir(self.db, drive_type, account_name, dir_path)

    def _invoke_list_endpoint(self, payload_data: dict):
        route = _routes_by_path(settings_route.router).get(("/drama-lead-in/own-file/list", "POST"))
        self.assertIsNotNone(
            route,
            "契约 §4 要求新增 POST /system-settings/drama-lead-in/own-file/list",
        )
        payload_cls = getattr(lead_in_schemas, "DramaLeadInOwnDirIn", None)
        self.assertIsNotNone(payload_cls, "缺少目录浏览请求模型 DramaLeadInOwnDirIn")
        payload = payload_cls(**payload_data)
        args = []
        for name in inspect.signature(route.endpoint).parameters:
            if name in ("payload", "body", "data"):
                args.append(payload)
            elif name == "request":
                args.append(_request())
            elif name == "current":
                args.append(_current_user())
            elif name == "db":
                args.append(self.db)
            else:
                self.fail(f"目录浏览路由出现未预期参数: {name}")
        return route.endpoint(*args)

    def _save_own_file_settings(self, file_path: str = EXISTING_MATERIAL_PATH, file_name: str = "引流说明.txt"):
        payload = DramaLeadInSettingsUpdateIn(
            enabled=True,
            materials=[
                DramaLeadInMaterialIn(
                    drive_type=DRIVE_TYPE,
                    mode="own_file",
                    account_name=ACCOUNT,
                    file_path=file_path,
                    file_name=file_name,
                )
            ],
        )
        return settings_route.put_drama_lead_in_settings(payload, _request(), _current_user(), self.db)

    def _settings_snapshot(self) -> str:
        return json.dumps(svc.get_drama_lead_in_materials(self.db), ensure_ascii=False, sort_keys=True)

    def _audit_actions(self) -> list[str]:
        return [row.action for row in self.db.query(AuditLog).all()]

    # ------------------------------------------------------------------
    # 1. 接口成功：根目录 / 子目录 / 空路径归一化
    # ------------------------------------------------------------------

    def test_root_browse_returns_entries(self) -> None:
        result = _dump(self._invoke_list_endpoint({"drive_type": DRIVE_TYPE, "account_name": ACCOUNT, "dir_path": "/"}))
        self.assertEqual(result["path"], "/")
        self.assertEqual(result["fid"], "0")
        self.assertEqual(len(result["entries"]), 2)
        first = result["entries"][0]
        self.assertEqual(first["name"], "素材")
        self.assertTrue(first["dir"])
        self.assertEqual(first["path"], "/素材")
        second = result["entries"][1]
        self.assertFalse(second["dir"])
        self.assertEqual(second["path"], "/根目录说明.txt")

    def test_subdirectory_browse_lists_files(self) -> None:
        result = _dump(self._invoke_list_endpoint(
            {"drive_type": DRIVE_TYPE, "account_name": ACCOUNT, "dir_path": BROWSE_DIR}
        ))
        self.assertEqual(result["path"], BROWSE_DIR)
        self.assertEqual(result["fid"], "dir-素材")
        self.assertEqual(len(result["entries"]), 1)
        entry = result["entries"][0]
        self.assertEqual(entry["name"], "引流说明.txt")
        self.assertFalse(entry["dir"])
        self.assertEqual(entry["size"], 1024)
        self.assertEqual(entry["path"], "/素材/引流说明.txt")
        self.adapter.lead_in_list_own_dir.assert_called_with(BROWSE_DIR)

    def test_empty_dir_path_is_normalized_to_root(self) -> None:
        result = _dump(self._invoke_list_endpoint({"drive_type": DRIVE_TYPE, "account_name": ACCOUNT, "dir_path": ""}))
        self.assertEqual(result["path"], "/")
        called_path = self.adapter.lead_in_list_own_dir.call_args[0][0]
        self.assertEqual(called_path, "/")

    def test_response_schema_fields(self) -> None:
        result = _dump(self._invoke_list_endpoint({"drive_type": DRIVE_TYPE, "account_name": ACCOUNT, "dir_path": "/"}))
        model = getattr(lead_in_schemas, "DramaLeadInOwnDirOut", None)
        self.assertIsNotNone(model, "缺少目录浏览响应模型 DramaLeadInOwnDirOut")
        self.assertEqual(set(result.keys()), set(model.model_fields.keys()))
        entry_model = getattr(lead_in_schemas, "DramaLeadInOwnDirEntryOut", None)
        self.assertIsNotNone(entry_model, "缺少目录浏览条目模型 DramaLeadInOwnDirEntryOut")
        self.assertEqual(set(result["entries"][0].keys()), set(entry_model.model_fields.keys()))

    # ------------------------------------------------------------------
    # 2. 选中文件 → resolve → 保存配置
    # ------------------------------------------------------------------

    def test_browsed_file_resolves_and_saves_own_file_config(self) -> None:
        listing = _dump(self._invoke_list_endpoint(
            {"drive_type": DRIVE_TYPE, "account_name": ACCOUNT, "dir_path": BROWSE_DIR}
        ))
        selected = listing["entries"][0]
        self.assertEqual(selected["path"], "/素材/引流说明.txt")

        # 前端回填后调用现有 own-file resolve 校验。
        resolve_route = _routes_by_path(settings_route.router).get(("/drama-lead-in/own-file/resolve", "POST"))
        self.assertIsNotNone(resolve_route, "目录浏览不得移除现有 own-file resolve 接口")
        file_info = svc.resolve_drama_lead_in_own_file(self.db, DRIVE_TYPE, ACCOUNT, selected["path"])
        self.assertFalse(file_info["file"]["dir"])
        self.assertEqual(file_info["file"]["path"], selected["path"])

        payload = self._save_own_file_settings(file_path=selected["path"], file_name=selected["name"])
        dump = _dump(payload)
        drive = next(item for item in dump["drives"] if item["drive_type"] == DRIVE_TYPE)
        self.assertEqual(drive["mode"], "own_file")
        self.assertTrue(drive["configured"])
        self.assertEqual(drive["file_path"], selected["path"])
        self.assertEqual(drive["file_name"], selected["name"])

    # ------------------------------------------------------------------
    # 3. 只读性：不写库 / 不产生任务 / 不触发写入
    # ------------------------------------------------------------------

    def test_browse_is_read_only(self) -> None:
        self._save_own_file_settings()
        settings_before = self._settings_snapshot()
        task_count_before = self.db.query(Task).count()
        record_count_before = self.db.query(TaskLeadInRecord).count()
        audit_count_before = self.db.query(AuditLog).count()

        self._list_dir("/")
        self._list_dir(BROWSE_DIR)
        self._invoke_list_endpoint({"drive_type": DRIVE_TYPE, "account_name": ACCOUNT, "dir_path": "/"})

        self.assertEqual(self._settings_snapshot(), settings_before)
        self.assertEqual(self.db.query(Task).count(), task_count_before)
        self.assertEqual(self.db.query(TaskLeadInRecord).count(), record_count_before)
        self.assertEqual(self.db.query(AuditLog).count(), audit_count_before + 1)  # 仅路由审计
        self.adapter.lead_in_add.assert_not_called()
        self.adapter.lead_in_add_own_file.assert_not_called()

    # ------------------------------------------------------------------
    # 4. 审计脱敏：只有 drive_type/account_name/条目数，无路径与文件名
    # ------------------------------------------------------------------

    def test_browse_audit_redacts_private_paths(self) -> None:
        before = set(row.id for row in self.db.query(AuditLog).all())
        self._invoke_list_endpoint({"drive_type": DRIVE_TYPE, "account_name": ACCOUNT, "dir_path": BROWSE_DIR})
        new_rows = [row for row in self.db.query(AuditLog).all() if row.id not in before]
        self.assertEqual(len(new_rows), 1, "目录浏览应写一条审计")
        row = new_rows[0]
        self.assertIn("lead_in", str(row.action or "").lower())

        detail_text = row.detail or ""
        detail = json.loads(detail_text) if detail_text else {}
        self.assertEqual(str(detail.get("drive_type")), DRIVE_TYPE)
        self.assertEqual(str(detail.get("account_name")), ACCOUNT)
        count_keys = ("entry_count", "count", "entries_count", "item_count")
        found = [detail[key] for key in count_keys if key in detail]
        self.assertTrue(found, f"审计 detail 缺少条目数: {detail}")
        self.assertEqual(int(found[0]), 1)
        # 不记录完整路径、文件名清单、链接或提取码。
        for forbidden in ("/素材", "引流说明.txt", "shareurl", "passcode", "password"):
            self.assertNotIn(forbidden, detail_text)
        allowed = {"drive_type", "account_name", *count_keys}
        self.assertTrue(set(detail.keys()) <= allowed, f"审计 detail 出现多余字段: {detail}")

    # ------------------------------------------------------------------
    # 5. 错误原因（中文）
    # ------------------------------------------------------------------

    def test_account_unavailable_reports_chinese_reason(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self._list_dir("/", account_name="missing-account")
        self.assertEqual(ctx.exception.code, "LEAD_IN_ACCOUNT_UNAVAILABLE")
        self.assertIn("账号", ctx.exception.message)
        self.adapter.lead_in_list_own_dir.assert_not_called()

    def test_disabled_account_reports_chinese_reason(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self._list_dir("/", account_name=DISABLED_ACCOUNT)
        self.assertEqual(ctx.exception.code, "LEAD_IN_ACCOUNT_UNAVAILABLE")
        self.assertIn("账号", ctx.exception.message)

    def test_directory_not_found_reports_chinese_reason(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self._list_dir("/不存在")
        self.assertEqual(ctx.exception.code, "LEAD_IN_DIR_NOT_FOUND")
        self.assertIn("目录不存在", ctx.exception.message)

    def test_directory_list_failure_reports_chinese_reason(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self._list_dir("/读取失败")
        self.assertEqual(ctx.exception.code, "LEAD_IN_DIR_LIST_FAILED")
        self.assertIn("读取目录失败", ctx.exception.message)

    def test_directory_permission_failure_reports_chinese_reason(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self._list_dir("/无权限")
        self.assertEqual(ctx.exception.code, "LEAD_IN_ACCOUNT_UNAVAILABLE")
        self.assertIn("权限", ctx.exception.message)

    def test_unsupported_drive_reports_chinese_reason(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self._list_dir("/", drive_type=NO_BROWSE_DRIVE_TYPE)
        self.assertEqual(ctx.exception.code, "LEAD_IN_UNSUPPORTED")
        self.assertIn("暂不支持浏览本账号目录", ctx.exception.message)
        self.adapter.lead_in_list_own_dir.assert_not_called()

    def test_manual_path_still_works_when_browse_unsupported(self) -> None:
        """契约 §7.2：未声明浏览能力的网盘只是隐藏「浏览」按钮，手填路径的读取/保存仍可用。"""
        account = DriveAccount(
            name="nobrowse-account",
            drive_type=NO_BROWSE_DRIVE_TYPE,
            cookie="mock-cookie",
            config_json=json.dumps({"cookie": "mock-cookie"}),
            enabled=True,
            is_default=True,
            runtime_status="active",
        )
        self.db.add(account)
        self.db.commit()

        file_info = svc.resolve_drama_lead_in_own_file(
            self.db, NO_BROWSE_DRIVE_TYPE, "nobrowse-account", EXISTING_MATERIAL_PATH
        )
        self.assertEqual(file_info["file"]["path"], EXISTING_MATERIAL_PATH)

        payload = settings_route.put_drama_lead_in_settings(
            DramaLeadInSettingsUpdateIn(
                enabled=True,
                materials=[
                    DramaLeadInMaterialIn(
                        drive_type=NO_BROWSE_DRIVE_TYPE,
                        mode="own_file",
                        account_name="nobrowse-account",
                        file_path=EXISTING_MATERIAL_PATH,
                        file_name="引流说明.txt",
                    )
                ],
            ),
            _request(),
            _current_user(),
            self.db,
        )
        drive = next(item for item in _dump(payload)["drives"] if item["drive_type"] == NO_BROWSE_DRIVE_TYPE)
        self.assertEqual(drive["mode"], "own_file")
        self.assertTrue(drive["configured"])
        self.assertFalse(drive["own_file_browse_supported"])
        # 浏览能力仍然不可用，避免接口越权放行。
        with self.assertRaises(ApiError) as ctx:
            self._list_dir("/", drive_type=NO_BROWSE_DRIVE_TYPE, account_name="nobrowse-account")
        self.assertEqual(ctx.exception.code, "LEAD_IN_UNSUPPORTED")

    def test_invalid_path_rejected(self) -> None:
        # 「//」按契约由适配器合并重复斜杠，不属于非法路径，故不在此断言。
        for bad_path in ("素材", "http://example.com/x", "../素材"):
            with self.subTest(path=bad_path):
                with self.assertRaises(ApiError) as ctx:
                    self._list_dir(bad_path)
                self.assertEqual(ctx.exception.code, "LEAD_IN_DIR_INVALID")
                self.assertTrue(ctx.exception.message)

    def test_timeout_maps_to_lead_in_timeout(self) -> None:
        with self.assertRaises(ApiError) as ctx:
            self._list_dir("/超时")
        self.assertEqual(ctx.exception.code, "LEAD_IN_TIMEOUT")

    # ------------------------------------------------------------------
    # 6. 权限、能力清单
    # ------------------------------------------------------------------

    def test_route_declares_task_write_permission(self) -> None:
        route = _routes_by_path(settings_route.router).get(("/drama-lead-in/own-file/list", "POST"))
        self.assertIsNotNone(route)
        self.assertIn(TASK_WRITE, _collect_permission_codes(route))

    def test_settings_payload_exposes_browse_capability(self) -> None:
        self._save_own_file_settings()
        payload = _dump(settings_route.get_drama_lead_in_settings(self.db))
        drives = {item["drive_type"]: item for item in payload["drives"]}
        self.assertIn(DRIVE_TYPE, drives)
        self.assertTrue(drives[DRIVE_TYPE]["own_file_browse_supported"])
        self.assertIn(NO_BROWSE_DRIVE_TYPE, drives)
        self.assertFalse(drives[NO_BROWSE_DRIVE_TYPE]["own_file_browse_supported"])
        # 契约 §2.3：夸克/百度声明浏览能力。
        for drive_type in ("quark", "baidu"):
            if drive_type in drives:
                self.assertTrue(
                    drives[drive_type]["own_file_browse_supported"],
                    f"{drive_type} 未声明目录浏览能力",
                )


if __name__ == "__main__":
    unittest.main()
