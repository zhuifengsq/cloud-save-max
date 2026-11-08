# -*- coding: utf-8 -*-
"""夸克 / 百度网盘适配器「追剧任务引流文件」能力测试。

全部使用假响应与 Mock，不访问真实网络；验证五个引流方法的参数、
返回字段与错误码映射。
"""
import copy
import unittest
from unittest.mock import Mock

from app.extensions.adapters.quark_adapter import QuarkAdapter
from app.extensions.adapters.baidu_adapter import BaiduAdapter


class FakeResponse:
    """模拟 requests.Response 的 json() 与 text"""

    def __init__(self, payload=None, text=""):
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return copy.deepcopy(self._payload)


class FakeQuarkTransport:
    """模拟夸克 HTTP 响应，按接口路径路由"""

    def __init__(self):
        self.token = {"status": 200, "code": 0, "message": "", "data": {"stoken": "stoken-1"}}
        self.detail_pages = {}
        self.listing_pages = {}
        self.path_list = []
        self.save = {"status": 200, "code": 0, "message": "", "data": {"task_id": "task-1"}}
        self.task = {"status": 200, "code": 0, "message": "", "data": {"status": 2}}
        self.calls = []

    def __call__(self, method, url, **kwargs):
        params = kwargs.get("params") or {}
        self.calls.append({"method": method, "url": url, "params": params, "kwargs": kwargs})
        if url.endswith("/share/sharepage/token"):
            return FakeResponse(self.token)
        if url.endswith("/share/sharepage/detail"):
            page = int(params.get("_page") or 1)
            default = {"code": 0, "message": "success", "data": {"list": []}, "metadata": {"_total": 0}}
            return FakeResponse(self.detail_pages.get(page, default))
        if url.endswith("/file/sort"):
            page = int(params.get("_page") or 1)
            default = {"code": 0, "message": "success", "data": {"list": []}, "metadata": {"_total": 0}}
            return FakeResponse(self.listing_pages.get(page, default))
        if url.endswith("/file/info/path_list"):
            return FakeResponse({"code": 0, "message": "success", "data": copy.deepcopy(self.path_list)})
        if url.endswith("/share/sharepage/save"):
            return FakeResponse(self.save)
        if url.endswith("/task"):
            return FakeResponse(self.task)
        raise AssertionError(f"未预期的夸克接口: {url}")

    def calls_to(self, suffix: str):
        return [call for call in self.calls if call["url"].endswith(suffix)]


def _quark_detail(items):
    return {"code": 0, "message": "success", "data": {"list": items}, "metadata": {"_total": len(items)}}


def _quark_listing(items):
    return {"code": 0, "message": "success", "data": {"list": items}, "metadata": {"_total": len(items)}}


def _quark_file(fid="f1", name="剧集01.mp4", size=100, token="tok-1", md5="aaaa"):
    return {
        "fid": fid,
        "file_name": name,
        "size": size,
        "dir": False,
        "file_type": 1,
        "share_fid_token": token,
        "md5": md5,
    }


def _quark_folder(fid="d1", name="合集"):
    return {"fid": fid, "file_name": name, "size": 0, "dir": True, "file_type": 0}


class TestQuarkLeadIn(unittest.TestCase):
    """夸克网盘引流能力"""

    def setUp(self):
        self.transport = FakeQuarkTransport()
        self.adapter = QuarkAdapter(cookie="__puus=test")
        self.adapter.LEAD_IN_MAX_ATTEMPTS = 3
        self.adapter.LEAD_IN_POLL_INTERVAL = 0.0
        self.adapter._send_request = self.transport
        self.target = {"dir": {"fid": "dir-1", "path": "/我的剧集", "name": "我的剧集"}}

    # ---------- 能力声明 ----------
    def test_capability_declared(self):
        self.assertTrue(QuarkAdapter.SUPPORTS_DRAMA_LEAD_IN)
        capability = self.adapter.lead_in_capability()
        self.assertEqual(capability["drive_type"], "quark")
        self.assertTrue(capability["supported"])
        self.assertEqual(capability["reason"], "")

    # ---------- resolve_material ----------
    def test_resolve_material_single_file_auto_selected(self):
        self.transport.detail_pages[1] = _quark_detail([_quark_file()])
        result = self.adapter.lead_in_resolve_material("https://pan.quark.cn/s/abcdef")
        self.assertEqual(result["code"], 0)
        data = result["data"]
        self.assertEqual(data["pwd_id"], "abcdef")
        self.assertEqual(data["stoken"], "stoken-1")
        self.assertEqual(data["material"]["fid"], "f1")
        self.assertEqual(data["material"]["name"], "剧集01.mp4")
        self.assertEqual(data["material"]["size"], 100)
        self.assertEqual(data["material"]["material_token"], "tok-1")
        self.assertEqual(data["material"]["md5"], "aaaa")
        self.assertEqual(data["material"]["pwd_id"], "abcdef")
        self.assertEqual(data["material"]["stoken"], "stoken-1")
        self.assertEqual(data["files"], [{"fid": "f1", "name": "剧集01.mp4", "size": 100, "dir": False, "path": "/剧集01.mp4"}])
        # 只请求一次列表，不会整目录转存
        self.assertEqual(len(self.transport.calls_to("/share/sharepage/detail")), 1)

    def test_resolve_material_multiple_requires_selection(self):
        self.transport.detail_pages[1] = _quark_detail([_quark_file(), _quark_file("f2", "剧集02.mp4", 200)])
        result = self.adapter.lead_in_resolve_material("https://pan.quark.cn/s/abcdef")
        self.assertEqual(result["code"], "LEAD_IN_MATERIAL_AMBIGUOUS")
        self.assertIn("选择", result["message"])
        # 歧义时仍要回传完整列表（含 path），前端据此让用户选择
        self.assertEqual(
            [item["fid"] for item in result["data"]["files"]],
            ["f1", "f2"],
        )
        self.assertNotIn("material", result["data"])
        with_folder = _quark_detail([_quark_file(), _quark_folder()])
        self.transport.detail_pages[1] = with_folder
        result = self.adapter.lead_in_resolve_material("https://pan.quark.cn/s/abcdef")
        self.assertEqual(result["code"], "LEAD_IN_MATERIAL_AMBIGUOUS")
        # files 里也要列出文件夹条目，但 dir=True
        files = result["data"]["files"]
        self.assertEqual([item["fid"] for item in files], ["f1", "d1"])
        self.assertTrue(files[1]["dir"])

    def test_resolve_material_selected_fid(self):
        self.transport.detail_pages[1] = _quark_detail([_quark_file(), _quark_file("f2", "剧集02.mp4", 200)])
        result = self.adapter.lead_in_resolve_material(
            "https://pan.quark.cn/s/abcdef", "", "f2"
        )
        self.assertEqual(result["code"], 0)
        self.assertEqual(result["data"]["material"]["fid"], "f2")
        self.assertEqual(result["data"]["material"]["name"], "剧集02.mp4")

    def test_resolve_material_reject_folder_selection(self):
        self.transport.detail_pages[1] = _quark_detail([_quark_file(), _quark_folder()])
        result = self.adapter.lead_in_resolve_material(
            "https://pan.quark.cn/s/abcdef", "", "d1"
        )
        self.assertEqual(result["code"], "LEAD_IN_MATERIAL_UNAVAILABLE")
        self.assertIn("文件夹", result["message"])

    def test_resolve_material_without_any_file(self):
        self.transport.detail_pages[1] = _quark_detail([_quark_folder()])
        result = self.adapter.lead_in_resolve_material("https://pan.quark.cn/s/abcdef")
        self.assertEqual(result["code"], "LEAD_IN_MATERIAL_UNAVAILABLE")
        self.assertIn("没有可用", result["message"])

    def test_resolve_material_passcode_required(self):
        self.transport.token = {"status": 400, "code": 41006, "message": "需要提取码", "data": {}}
        result = self.adapter.lead_in_resolve_material("https://pan.quark.cn/s/abcdef")
        self.assertEqual(result["code"], "LEAD_IN_PASSCODE_REQUIRED")

    def test_resolve_material_passcode_invalid(self):
        self.transport.token = {"status": 400, "code": 41006, "message": "提取码错误", "data": {}}
        result = self.adapter.lead_in_resolve_material("https://pan.quark.cn/s/abcdef", "1234")
        self.assertEqual(result["code"], "LEAD_IN_PASSCODE_INVALID")
        self.assertIn("提取码", result["message"])

    def test_resolve_material_unavailable(self):
        self.transport.token = {"status": 400, "code": 41004, "message": "分享已失效", "data": {}}
        result = self.adapter.lead_in_resolve_material("https://pan.quark.cn/s/abcdef")
        self.assertEqual(result["code"], "LEAD_IN_MATERIAL_UNAVAILABLE")

    def test_resolve_material_token_failed(self):
        self.transport.token = {"status": 500, "code": 999, "message": "服务繁忙", "data": {}}
        result = self.adapter.lead_in_resolve_material("https://pan.quark.cn/s/abcdef")
        self.assertEqual(result["code"], "LEAD_IN_FAILED")

    # ---------- resolve_dir ----------
    def test_resolve_dir_ok(self):
        self.transport.path_list = [{"file_path": "/我的剧集", "fid": "dir-1", "dir": True}]
        self.transport.listing_pages[1] = _quark_listing([_quark_file()])
        result = self.adapter.lead_in_resolve_dir("/我的剧集")
        self.assertEqual(result["code"], 0)
        self.assertEqual(result["data"]["dir"], {"fid": "dir-1", "path": "/我的剧集", "name": "我的剧集"})
        self.assertEqual(
            result["data"]["entries"],
            [{"fid": "f1", "name": "剧集01.mp4", "size": 100, "dir": False}],
        )

    def test_resolve_dir_normalize_backslash(self):
        self.transport.path_list = [{"file_path": "/剧集/2026", "fid": "dir-2", "dir": True}]
        self.transport.listing_pages[1] = _quark_listing([])
        result = self.adapter.lead_in_resolve_dir("剧集\\2026")
        self.assertEqual(result["code"], 0)
        self.assertEqual(result["data"]["dir"]["path"], "/剧集/2026")
        self.assertEqual(result["data"]["entries"], [])

    def test_resolve_dir_not_found(self):
        self.transport.path_list = []
        result = self.adapter.lead_in_resolve_dir("/不存在的目录")
        self.assertEqual(result["code"], "LEAD_IN_DIR_NOT_FOUND")

    def test_resolve_dir_reject_file(self):
        self.transport.path_list = [{"file_path": "/我的剧集/a.mp4", "fid": "x", "dir": False, "file_type": 1}]
        result = self.adapter.lead_in_resolve_dir("/我的剧集/a.mp4")
        self.assertEqual(result["code"], "LEAD_IN_DIR_INVALID")
        self.assertEqual(self.transport.calls_to("/file/sort"), [])

    def test_resolve_dir_reject_root(self):
        for bad in ("", "/", "\\", "https://pan.quark.cn/s/abc"):
            result = self.adapter.lead_in_resolve_dir(bad)
            self.assertEqual(result["code"], "LEAD_IN_DIR_INVALID", bad)

    def test_resolve_dir_listing_failure_invalid(self):
        self.transport.path_list = [{"file_path": "/我的剧集", "fid": "dir-1", "dir": True}]
        self.transport.listing_pages[1] = {"code": 400, "message": "目录不存在"}
        result = self.adapter.lead_in_resolve_dir("/我的剧集")
        self.assertEqual(result["code"], "LEAD_IN_DIR_INVALID")

    def test_resolve_dir_tolerates_leading_space_in_dir_name(self):
        """精确路径定位失败时按目录名兜底（网盘目录名带首尾空格）"""
        self.transport.path_list = []
        self.adapter.ls_dir = Mock(
            side_effect=[
                _quark_listing([{"fid": "d1", "file_name": "自动转存", "dir": True}]),
                _quark_listing([{"fid": "d2", "file_name": " 走马 | 上任", "dir": True}]),
                _quark_listing([]),
            ]
        )
        result = self.adapter.lead_in_resolve_dir("/自动转存/走马 | 上任")
        self.assertEqual(result["code"], 0)
        self.assertEqual(
            result["data"]["dir"],
            {"fid": "d2", "path": "/自动转存/ 走马 | 上任", "name": " 走马 | 上任"},
        )
        self.assertEqual(result["data"]["entries"], [])

    # ---------- inspect ----------
    def test_inspect_absent(self):
        self.adapter.ls_dir = Mock(return_value=_quark_listing([]))
        result = self.adapter.lead_in_inspect({"fid": "f1", "name": "剧集01.mp4", "size": 100}, self.target)
        self.assertEqual(result["code"], 0)
        self.assertEqual(result["data"]["state"], "absent")
        self.assertIsNone(result["data"]["existing"])
        self.adapter.ls_dir.assert_called_once_with("dir-1", max_items=0)

    def test_inspect_exists_for_same_file(self):
        self.adapter.ls_dir = Mock(return_value=_quark_listing([_quark_file("saved-1", "剧集01.mp4", 100)]))
        result = self.adapter.lead_in_inspect({"fid": "f1", "name": "剧集01.mp4", "size": 100}, self.target)
        self.assertEqual(result["data"]["state"], "exists")
        self.assertEqual(result["data"]["existing"]["fid"], "saved-1")

    def test_inspect_conflict_when_not_same_file(self):
        self.adapter.ls_dir = Mock(return_value=_quark_listing([_quark_file("saved-1", "剧集01.mp4", 999)]))
        result = self.adapter.lead_in_inspect({"fid": "f1", "name": "剧集01.mp4", "size": 100}, self.target)
        self.assertEqual(result["data"]["state"], "conflict")
        self.assertIn("未覆盖", result["data"]["message"])

    def test_inspect_uses_target_snapshot_when_listing_fails(self):
        self.adapter.ls_dir = Mock(side_effect=RuntimeError("网络异常"))
        target = {
            "dir": {"fid": "dir-1", "path": "/我的剧集", "name": "我的剧集"},
            "entries": [{"fid": "saved-1", "name": "剧集01.mp4", "size": 100, "dir": False}],
        }
        result = self.adapter.lead_in_inspect({"fid": "f1", "name": "剧集01.mp4", "size": 100}, target)
        self.assertEqual(result["data"]["state"], "exists")

    # ---------- add ----------
    def test_add_submits_selected_file_only(self):
        self.adapter.save_file = Mock(return_value={"code": 0, "data": {"task_id": "task-9"}})
        material = {
            "fid": "f1",
            "name": "剧集01.mp4",
            "size": 100,
            "path": "/剧集01.mp4",
            "material_token": "tok-1",
            "md5": "aaaa",
            "pwd_id": "abcdef",
            "stoken": "stoken-1",
        }
        result = self.adapter.lead_in_add(material, self.target)
        self.assertEqual(result["code"], 0)
        self.assertFalse(result["data"]["sync"])
        self.assertEqual(result["data"]["handle"]["task_id"], "task-9")
        self.assertEqual(result["data"]["handle"]["name"], "剧集01.mp4")
        self.assertEqual(result["data"]["handle"]["dir_fid"], "dir-1")
        self.adapter.save_file.assert_called_once_with(["f1"], ["tok-1"], "dir-1", "abcdef", "stoken-1")

    def test_add_resolves_material_when_token_missing(self):
        self.transport.detail_pages[1] = _quark_detail([_quark_file()])
        self.adapter.save_file = Mock(return_value={"code": 0, "data": {"task_id": "task-9"}})
        material = {
            "fid": "f1",
            "name": "剧集01.mp4",
            "size": 100,
            "shareurl": "https://pan.quark.cn/s/abcdef",
        }
        result = self.adapter.lead_in_add(material, self.target)
        self.assertEqual(result["code"], 0)
        self.adapter.save_file.assert_called_once_with(["f1"], ["tok-1"], "dir-1", "abcdef", "stoken-1")

    def test_add_name_conflict(self):
        self.adapter.save_file = Mock(return_value={"code": 400, "message": "同名文件已存在"})
        material = {"fid": "f1", "name": "剧集01.mp4", "size": 100, "material_token": "tok-1", "pwd_id": "p", "stoken": "s"}
        result = self.adapter.lead_in_add(material, self.target)
        self.assertEqual(result["code"], "LEAD_IN_NAME_CONFLICT")
        self.assertIsNone(result["data"])

    def test_add_missing_task_id(self):
        self.adapter.save_file = Mock(return_value={"code": 0, "data": {}})
        material = {"fid": "f1", "name": "剧集01.mp4", "size": 100, "material_token": "tok-1", "pwd_id": "p", "stoken": "s"}
        result = self.adapter.lead_in_add(material, self.target)
        self.assertEqual(result["code"], "LEAD_IN_FAILED")

    def test_add_request_exception_maps_to_timeout(self):
        self.adapter.save_file = Mock(side_effect=TimeoutError("请求超时"))
        material = {"fid": "f1", "name": "剧集01.mp4", "size": 100, "material_token": "tok-1", "pwd_id": "p", "stoken": "s"}
        result = self.adapter.lead_in_add(material, self.target)
        self.assertEqual(result["code"], "LEAD_IN_TIMEOUT")
        self.assertIn("待确认", result["message"])

    # ---------- verify ----------
    def test_verify_added(self):
        self.adapter.ls_dir = Mock(return_value=_quark_listing([_quark_file("saved-1", "剧集01.mp4", 100)]))
        self.transport.task = {"status": 200, "code": 0, "data": {"status": 1}}
        material = {"fid": "f1", "name": "剧集01.mp4", "size": 100}
        result = self.adapter.lead_in_verify(material, self.target, {"task_id": "task-9"})
        self.assertEqual(result["data"]["state"], "added")
        self.assertEqual(result["data"]["fid"], "saved-1")
        self.assertEqual(self.transport.calls_to("/task"), [])

    def test_verify_pending_is_bounded(self):
        self.adapter.ls_dir = Mock(return_value=_quark_listing([]))
        self.transport.task = {"status": 200, "code": 0, "data": {"status": 1}}
        material = {"fid": "f1", "name": "剧集01.mp4", "size": 100}
        result = self.adapter.lead_in_verify(material, self.target, {"task_id": "task-9"})
        self.assertEqual(result["data"]["state"], "pending")
        self.assertIn("待确认", result["data"]["message"])
        # 有界查询：最多 LEAD_IN_MAX_ATTEMPTS 次
        self.assertEqual(len(self.transport.calls_to("/task")), 3)
        self.assertEqual(self.adapter.ls_dir.call_count, 3)

    def test_verify_failed_task(self):
        self.adapter.ls_dir = Mock(return_value=_quark_listing([]))
        self.transport.task = {"status": 200, "code": 0, "data": {"status": 4, "message": "转存失败"}}
        material = {"fid": "f1", "name": "剧集01.mp4", "size": 100}
        result = self.adapter.lead_in_verify(material, self.target, {"task_id": "task-9"})
        self.assertEqual(result["data"]["state"], "failed")
        self.assertIn("转存失败", result["data"]["message"])

    def test_verify_done_but_missing(self):
        self.adapter.ls_dir = Mock(return_value=_quark_listing([]))
        self.transport.task = {"status": 200, "code": 0, "data": {"status": 2}}
        material = {"fid": "f1", "name": "剧集01.mp4", "size": 100}
        result = self.adapter.lead_in_verify(material, self.target, {"task_id": "task-9"})
        self.assertEqual(result["data"]["state"], "failed")
        self.assertIn("未找到", result["data"]["message"])

    def test_verify_conflict_same_name_different_size(self):
        self.adapter.ls_dir = Mock(return_value=_quark_listing([_quark_file("saved-1", "剧集01.mp4", 999)]))
        material = {"fid": "f1", "name": "剧集01.mp4", "size": 100}
        result = self.adapter.lead_in_verify(material, self.target, {"task_id": "task-9"})
        self.assertEqual(result["data"]["state"], "failed")
        self.assertIn("同名", result["data"]["message"])


class FakeBaiduTransport:
    """模拟百度 PCS 目录列表接口"""

    def __init__(self):
        self.listings = {}
        self.listing_pages = {}
        self.calls = []

    def __call__(self, method, url, params=None, headers=None, data=None, **kwargs):
        self.calls.append({"method": method, "url": url, "params": params, "data": data})
        if url.endswith("/api/list"):
            remotepath = (params or {}).get("dir") or "/"
            page = int((params or {}).get("page") or 1)
            payload = self.listing_pages.get((remotepath, page))
            if payload is None:
                payload = self.listings.get(remotepath, {"errno": 0, "list": []})
            return FakeResponse(payload)
        raise AssertionError(f"未预期的百度接口: {url}")


def _baidu_share_file(fid="100", name="剧集01.mp4", size=100, path="/剧集01.mp4"):
    return {
        "fid": fid,
        "file_name": name,
        "size": size,
        "dir": False,
        "file_type": 1,
        "share_fid_token": fid,
        "path": path,
    }


class TestBaiduLeadIn(unittest.TestCase):
    """百度网盘引流能力"""

    def setUp(self):
        self.transport = FakeBaiduTransport()
        self.adapter = BaiduAdapter(cookie="BDUSS=test; STOKEN=test")
        self.adapter.LEAD_IN_MAX_ATTEMPTS = 3
        self.adapter.LEAD_IN_POLL_INTERVAL = 0.0
        self.adapter._request = self.transport
        self.target = {"dir": {"fid": "/我的剧集", "path": "/我的剧集", "name": "我的剧集"}}

    def _mock_share(self, items, token=None):
        self.adapter.get_stoken = Mock(
            return_value=token or {"status": 200, "code": 0, "data": {"stoken": "1abc:1234"}, "message": "success"}
        )
        self.adapter.get_detail = Mock(
            return_value={"code": 0, "message": "success", "data": {"list": items}, "metadata": {"_total": len(items)}}
        )

    # ---------- 能力声明 ----------
    def test_capability_declared(self):
        self.assertTrue(BaiduAdapter.SUPPORTS_DRAMA_LEAD_IN)
        capability = self.adapter.lead_in_capability()
        self.assertEqual(capability["drive_type"], "baidu")
        self.assertTrue(capability["supported"])

    # ---------- resolve_material ----------
    def test_resolve_material_single_file_auto_selected(self):
        self._mock_share([_baidu_share_file()])
        result = self.adapter.lead_in_resolve_material("https://pan.baidu.com/s/1abc")
        self.assertEqual(result["code"], 0)
        data = result["data"]
        self.assertEqual(data["pwd_id"], "1abc")
        self.assertEqual(data["stoken"], "1abc:1234")
        self.assertEqual(data["material"]["fid"], "100")
        self.assertEqual(data["material"]["name"], "剧集01.mp4")
        self.assertEqual(data["material"]["path"], "/剧集01.mp4")
        self.assertEqual(data["material"]["pwd_id"], "1abc")
        self.adapter.get_stoken.assert_called_once_with("1abc", "", check_errno=True)

    def test_resolve_material_multiple_requires_selection(self):
        folder = {"fid": "200", "file_name": "合集", "size": 0, "dir": True, "file_type": 0, "path": "/合集"}
        self._mock_share([_baidu_share_file(), folder])
        result = self.adapter.lead_in_resolve_material("https://pan.baidu.com/s/1abc")
        self.assertEqual(result["code"], "LEAD_IN_MATERIAL_AMBIGUOUS")
        self.assertIn("选择", result["message"])
        files = result["data"]["files"]
        self.assertEqual([item["fid"] for item in files], ["100", "200"])
        self.assertTrue(files[1]["dir"])

    def test_resolve_material_selected_fid(self):
        self._mock_share([_baidu_share_file(), _baidu_share_file("101", "剧集02.mp4", 200, "/剧集02.mp4")])
        result = self.adapter.lead_in_resolve_material("https://pan.baidu.com/s/1abc", "", "101")
        self.assertEqual(result["code"], 0)
        self.assertEqual(result["data"]["material"]["fid"], "101")
        self.assertEqual(result["data"]["material"]["name"], "剧集02.mp4")

    def test_resolve_material_passcode_required(self):
        self._mock_share([], token={"status": 400, "code": 4, "message": "分享提取码错误"})
        result = self.adapter.lead_in_resolve_material("https://pan.baidu.com/s/1abc")
        self.assertEqual(result["code"], "LEAD_IN_PASSCODE_REQUIRED")

    def test_resolve_material_passcode_invalid(self):
        self._mock_share([], token={"status": 400, "code": 200025, "message": "提取码错误"})
        result = self.adapter.lead_in_resolve_material("https://pan.baidu.com/s/1abc", "1234")
        self.assertEqual(result["code"], "LEAD_IN_PASSCODE_INVALID")

    def test_resolve_material_unavailable(self):
        self._mock_share([], token={"status": 400, "code": -1, "message": "分享链接不存在"})
        result = self.adapter.lead_in_resolve_material("https://pan.baidu.com/s/1abc")
        self.assertEqual(result["code"], "LEAD_IN_MATERIAL_UNAVAILABLE")

    def test_resolve_material_not_logged_in(self):
        self._mock_share([], token={"status": 500, "code": 1, "message": "百度网盘客户端未初始化"})
        result = self.adapter.lead_in_resolve_material("https://pan.baidu.com/s/1abc")
        self.assertEqual(result["code"], "LEAD_IN_FAILED")
        self.assertIn("账号", result["message"])

    # ---------- resolve_dir ----------
    def test_resolve_dir_ok(self):
        self.transport.listings = {
            "/": {"errno": 0, "list": [{"path": "/我的剧集", "isdir": 1, "fs_id": 1}]},
            "/我的剧集": {"errno": 0, "list": [{"path": "/我的剧集/旧文件.mp4", "isdir": 0, "fs_id": 2, "size": 5}]},
        }
        result = self.adapter.lead_in_resolve_dir("/我的剧集")
        self.assertEqual(result["code"], 0)
        self.assertEqual(result["data"]["dir"], {"fid": "/我的剧集", "path": "/我的剧集", "name": "我的剧集"})
        self.assertEqual(result["data"]["entries"], [{"fid": "/我的剧集/旧文件.mp4", "name": "旧文件.mp4", "size": 5, "dir": False}])

    def test_resolve_dir_not_found(self):
        self.transport.listings = {"/": {"errno": 0, "list": []}}
        result = self.adapter.lead_in_resolve_dir("/不存在的目录")
        self.assertEqual(result["code"], "LEAD_IN_DIR_NOT_FOUND")

    def test_resolve_dir_reject_file(self):
        self.transport.listings = {
            "/我的剧集": {"errno": 0, "list": [{"path": "/我的剧集/a.mp4", "isdir": 0, "fs_id": 2}]},
        }
        result = self.adapter.lead_in_resolve_dir("/我的剧集/a.mp4")
        self.assertEqual(result["code"], "LEAD_IN_DIR_INVALID")

    def test_resolve_dir_parent_not_found(self):
        self.transport.listings = {"/我的剧集": {"errno": -9, "list": []}}
        result = self.adapter.lead_in_resolve_dir("/我的剧集/2026")
        self.assertEqual(result["code"], "LEAD_IN_DIR_NOT_FOUND")

    def test_resolve_dir_parent_auth_failed(self):
        self.transport.listings = {"/我的剧集": {"errno": -6, "list": []}}
        result = self.adapter.lead_in_resolve_dir("/我的剧集/2026")
        self.assertEqual(result["code"], "LEAD_IN_FAILED")
        self.assertIn("认证失败", result["message"])

    def test_resolve_dir_reject_root(self):
        for bad in ("", "/", "\\"):
            self.assertEqual(self.adapter.lead_in_resolve_dir(bad)["code"], "LEAD_IN_DIR_INVALID", bad)

    def test_resolve_dir_keeps_segment_spaces(self):
        """目录名本身带首尾空格时按原样精确匹配（修复前会被 trim 成另一路径）"""
        spaced = "/自动转存/ 走马 | 上任"
        self.transport.listings = {
            "/自动转存": {"errno": 0, "list": [{"path": spaced, "isdir": 1, "fs_id": 3}]},
            spaced: {"errno": 0, "list": []},
        }
        result = self.adapter.lead_in_resolve_dir(spaced)
        self.assertEqual(result["code"], 0, result.get("message"))
        self.assertEqual(result["data"]["dir"], {"fid": spaced, "path": spaced, "name": " 走马 | 上任"})
        self.assertEqual(result["data"]["entries"], [])

    def test_resolve_dir_tolerates_leading_space_in_dir_name(self):
        """输入与网盘目录名只差首尾空白时也能命中，并使用网盘真实路径"""
        real = "/自动转存/ 走马 | 上任"
        self.transport.listings = {
            "/自动转存": {"errno": 0, "list": [{"path": real, "isdir": 1, "fs_id": 3}]},
            real: {"errno": 0, "list": []},
        }
        result = self.adapter.lead_in_resolve_dir("/自动转存/走马 | 上任")
        self.assertEqual(result["code"], 0, result.get("message"))
        self.assertEqual(result["data"]["dir"]["path"], real)

    def test_resolve_dir_reads_all_parent_pages(self):
        """父目录子项超过单页上限时不再误报目录不存在"""
        target = "/自动转存/第201个目录"
        page_one = [{"path": f"/自动转存/占位{i:03d}", "isdir": 1, "fs_id": 1000 + i} for i in range(200)]
        self.transport.listings = {target: {"errno": 0, "list": []}}
        self.transport.listing_pages[("/自动转存", 1)] = {"errno": 0, "list": page_one}
        self.transport.listing_pages[("/自动转存", 2)] = {
            "errno": 0,
            "list": [{"path": target, "isdir": 1, "fs_id": 2001}],
        }
        result = self.adapter.lead_in_resolve_dir(target)
        self.assertEqual(result["code"], 0, result.get("message"))
        self.assertEqual(result["data"]["dir"]["path"], target)

    def test_resolve_dir_falls_back_to_chain_when_parent_unreadable(self):
        """父目录列表读不到时逐级定位，并容忍首尾空白"""
        self.transport.listings = {
            "/": {"errno": 0, "list": [{"path": "/自动转存", "isdir": 1, "fs_id": 1}]},
            "/自动转存": {"errno": 0, "list": [{"path": "/自动转存/走马", "isdir": 1, "fs_id": 2}]},
            "/自动转存 ": {"errno": -9, "list": []},
            "/自动转存/走马": {"errno": 0, "list": []},
        }
        result = self.adapter.lead_in_resolve_dir("/自动转存 /走马")
        self.assertEqual(result["code"], 0, result.get("message"))
        self.assertEqual(result["data"]["dir"]["path"], "/自动转存/走马")

    # ---------- inspect ----------
    def _entries(self, items):
        return {"code": 0, "data": {"list": items}, "metadata": {"_total": len(items)}}

    def test_inspect_absent(self):
        self.adapter.ls_dir = Mock(return_value=self._entries([]))
        result = self.adapter.lead_in_inspect({"fid": "100", "name": "剧集01.mp4", "size": 100}, self.target)
        self.assertEqual(result["data"]["state"], "absent")
        self.adapter.ls_dir.assert_called_once_with("/我的剧集", max_items=0)

    def test_inspect_exists_for_same_file(self):
        entry = {"fid": "/我的剧集/剧集01.mp4", "file_name": "剧集01.mp4", "size": 100, "dir": False}
        self.adapter.ls_dir = Mock(return_value=self._entries([entry]))
        result = self.adapter.lead_in_inspect({"fid": "100", "name": "剧集01.mp4", "size": 100}, self.target)
        self.assertEqual(result["data"]["state"], "exists")
        self.assertEqual(result["data"]["existing"]["fid"], "/我的剧集/剧集01.mp4")

    def test_inspect_conflict_when_not_same_file(self):
        entry = {"fid": "/我的剧集/剧集01.mp4", "file_name": "剧集01.mp4", "size": 999, "dir": False}
        self.adapter.ls_dir = Mock(return_value=self._entries([entry]))
        result = self.adapter.lead_in_inspect({"fid": "100", "name": "剧集01.mp4", "size": 100}, self.target)
        self.assertEqual(result["data"]["state"], "conflict")

    # ---------- add ----------
    def test_add_submits_only_selected_file(self):
        self.adapter.save_file = Mock(
            return_value={"code": 0, "message": "success", "data": {"task_id": "baidu_sync_1abc", "save_as_top_fids": ["999"]}}
        )
        material = {
            "fid": "100",
            "name": "剧集01.mp4",
            "size": 100,
            "material_token": "100",
            "md5": "",
            "pwd_id": "1abc",
            "stoken": "1abc:1234",
        }
        result = self.adapter.lead_in_add(material, self.target)
        self.assertEqual(result["code"], 0)
        self.assertTrue(result["data"]["sync"])
        self.assertEqual(result["data"]["handle"]["task_id"], "baidu_sync_1abc")
        self.assertEqual(result["data"]["handle"]["fid"], "999")
        self.adapter.save_file.assert_called_once_with(
            ["100"], ["100"], "/我的剧集", "1abc", "1abc:1234", file_names=["剧集01.mp4"]
        )

    def test_add_name_conflict(self):
        self.adapter.save_file = Mock(return_value={"code": 31061, "message": "文件已存在", "data": {}})
        material = {"fid": "100", "name": "剧集01.mp4", "size": 100, "material_token": "100", "pwd_id": "1abc", "stoken": "1abc:1234"}
        result = self.adapter.lead_in_add(material, self.target)
        self.assertEqual(result["code"], "LEAD_IN_NAME_CONFLICT")

    def test_add_share_unavailable(self):
        self.adapter.save_file = Mock(return_value={"code": 145, "message": "分享链接已失效", "data": {}})
        material = {"fid": "100", "name": "剧集01.mp4", "size": 100, "material_token": "100", "pwd_id": "1abc", "stoken": "1abc:1234"}
        result = self.adapter.lead_in_add(material, self.target)
        self.assertEqual(result["code"], "LEAD_IN_MATERIAL_UNAVAILABLE")

    # ---------- verify ----------
    def test_verify_added(self):
        entry = {"fid": "/我的剧集/剧集01.mp4", "file_name": "剧集01.mp4", "size": 100, "dir": False}
        self.adapter.ls_dir = Mock(return_value=self._entries([entry]))
        material = {"fid": "100", "name": "剧集01.mp4", "size": 100}
        result = self.adapter.lead_in_verify(
            material, self.target, {"task_id": "baidu_sync_1abc", "fid": "999"}
        )
        self.assertEqual(result["data"]["state"], "added")
        self.assertEqual(result["data"]["fid"], "/我的剧集/剧集01.mp4")

    def test_verify_failed_when_done_but_missing(self):
        self.adapter.ls_dir = Mock(return_value=self._entries([]))
        material = {"fid": "100", "name": "剧集01.mp4", "size": 100}
        result = self.adapter.lead_in_verify(
            material, self.target, {"task_id": "baidu_sync_1abc", "fid": "999"}
        )
        self.assertEqual(result["data"]["state"], "failed")
        self.assertIn("未找到", result["data"]["message"])
        self.assertEqual(self.adapter.ls_dir.call_count, 3)

    def test_verify_conflict_same_name_different_size(self):
        entry = {"fid": "/我的剧集/剧集01.mp4", "file_name": "剧集01.mp4", "size": 999, "dir": False}
        self.adapter.ls_dir = Mock(return_value=self._entries([entry]))
        material = {"fid": "100", "name": "剧集01.mp4", "size": 100}
        result = self.adapter.lead_in_verify(
            material, self.target, {"task_id": "baidu_sync_1abc", "fid": "999"}
        )
        self.assertEqual(result["data"]["state"], "failed")
        self.assertIn("同名", result["data"]["message"])


if __name__ == "__main__":
    unittest.main()
