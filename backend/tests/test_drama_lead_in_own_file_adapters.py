# -*- coding: utf-8 -*-
"""夸克 / 百度网盘适配器「本账号文件」素材模式测试。

全部使用假响应与 Mock，不访问真实网络；验证 resolve/add 的参数、
返回字段、错误码映射，以及 inspect/verify 的复用行为。
"""
import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.extensions.adapters.base_adapter import BaseCloudDriveAdapter
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
    """模拟夸克 HTTP 响应：token/detail/sort/path_list/copy/task"""

    def __init__(self):
        self.token = {"status": 200, "code": 0, "message": "", "data": {"stoken": "stoken-1"}}
        self.detail_pages = {}
        self.listings = {}
        self.path_map = {}
        self.copy = {"status": 200, "code": 0, "message": "", "data": {"task_id": "copy-task-1"}}
        self.task = {"status": 200, "code": 0, "message": "", "data": {"status": 2}}
        self.calls = []

    def __call__(self, method, url, **kwargs):
        params = kwargs.get("params") or {}
        body = kwargs.get("json") or {}
        self.calls.append({"method": method, "url": url, "params": params, "json": body})
        if url.endswith("/share/sharepage/token"):
            return FakeResponse(self.token)
        if url.endswith("/share/sharepage/detail"):
            page = int(params.get("_page") or 1)
            default = {"code": 0, "message": "success", "data": {"list": []}, "metadata": {"_total": 0}}
            return FakeResponse(self.detail_pages.get(page, default))
        if url.endswith("/file/sort"):
            pdir_fid = str(params.get("pdir_fid") or "0")
            default = {"code": 0, "message": "success", "data": {"list": []}, "metadata": {"_total": 0}}
            return FakeResponse(self.listings.get(pdir_fid, default))
        if url.endswith("/file/info/path_list"):
            paths = body.get("file_path") or []
            items = [copy.deepcopy(self.path_map[p]) for p in paths if p in self.path_map]
            return FakeResponse({"code": 0, "message": "success", "data": items})
        if url.endswith("/file/copy"):
            return FakeResponse(self.copy)
        if url.endswith("/task"):
            return FakeResponse(self.task)
        raise AssertionError(f"未预期的夸克接口: {url}")

    def calls_to(self, suffix: str):
        return [call for call in self.calls if call["url"].endswith(suffix)]


def _quark_own_file(fid="f9", name="剧集01.mp4", size=1024, md5="abc123", file_type=1):
    return {
        "fid": fid,
        "file_name": name,
        "size": size,
        "dir": file_type == 0,
        "file_type": file_type,
        "md5": md5,
    }


def _quark_listing(items):
    return {"code": 0, "message": "success", "data": {"list": items}, "metadata": {"_total": len(items)}}


class TestQuarkOwnFile(unittest.TestCase):
    """夸克「本账号文件」模式"""

    def setUp(self):
        self.transport = FakeQuarkTransport()
        self.adapter = QuarkAdapter(cookie="__puus=test")
        self.adapter.LEAD_IN_MAX_ATTEMPTS = 3
        self.adapter.LEAD_IN_POLL_INTERVAL = 0.0
        self.adapter._send_request = self.transport
        self.target = {"dir": {"fid": "dir-1", "path": "/我的剧集", "name": "我的剧集"}}
        self.material = {
            "source": "own_file",
            "fid": "f9",
            "name": "剧集01.mp4",
            "path": "/电视剧/剧集01.mp4",
            "size": 1024,
            "md5": "abc123",
        }

    # ---------- 能力声明 ----------
    def test_capability_own_file_supported(self):
        capability = self.adapter.lead_in_capability()
        self.assertTrue(QuarkAdapter.SUPPORTS_LEAD_IN_OWN_FILE)
        self.assertTrue(capability["own_file_supported"])

    # ---------- resolve_own_file ----------
    def test_resolve_own_file_ok(self):
        # 真实夸克 path_list 对文件返回空列表、只对目录有效：
        # resolve 只允许查父目录，再在父目录精确列表里按同名文件定位。
        self.transport.path_map = {
            "/电视剧": {"file_path": "/电视剧", "fid": "d2", "dir": True, "file_type": 0},
        }
        self.transport.listings["d2"] = _quark_listing([_quark_own_file()])
        result = self.adapter.lead_in_resolve_own_file("/电视剧/剧集01.mp4")
        self.assertEqual(result["code"], 0)
        file_info = result["data"]["file"]
        self.assertEqual(file_info["fid"], "f9")
        self.assertEqual(file_info["name"], "剧集01.mp4")
        self.assertEqual(file_info["path"], "/电视剧/剧集01.mp4")
        self.assertEqual(file_info["size"], 1024)
        self.assertEqual(file_info["md5"], "abc123")
        self.assertFalse(file_info["dir"])
        # 只请求了父目录，未用 path_list 查文件，也未做任何按名全盘搜索
        path_calls = self.transport.calls_to("/file/info/path_list")
        self.assertEqual([call["json"]["file_path"] for call in path_calls], [["/电视剧"]])
        sort_calls = self.transport.calls_to("/file/sort")
        self.assertEqual(sort_calls[0]["params"]["pdir_fid"], "d2")

    def test_resolve_own_file_root_level(self):
        # 根目录文件：父目录固定 fid=0，同样只靠根目录列表精确匹配
        self.transport.listings["0"] = _quark_listing(
            [_quark_own_file(fid="f1", name="根目录素材.jpg", md5="rootmd5")]
        )
        result = self.adapter.lead_in_resolve_own_file("/根目录素材.jpg")
        self.assertEqual(result["code"], 0)
        file_info = result["data"]["file"]
        self.assertEqual(file_info["fid"], "f1")
        self.assertEqual(file_info["name"], "根目录素材.jpg")
        self.assertEqual(file_info["md5"], "rootmd5")
        self.assertEqual(self.transport.calls_to("/file/info/path_list"), [])

    def test_resolve_own_file_not_found(self):
        result = self.adapter.lead_in_resolve_own_file("/电视剧/不存在.mp4")
        self.assertEqual(result["code"], "LEAD_IN_MATERIAL_UNAVAILABLE")
        self.assertIn("素材文件不存在", result["message"])

    def test_resolve_own_file_reject_folder(self):
        # 父目录列表里同名条目是文件夹 -> 明确拒绝，不能当作文件素材
        self.transport.path_map = {
            "/电视剧": {"file_path": "/电视剧", "fid": "d2", "dir": True, "file_type": 0},
        }
        self.transport.listings["d2"] = _quark_listing(
            [{"fid": "d9", "file_name": "合集", "dir": True, "file_type": 0, "size": 0}]
        )
        result = self.adapter.lead_in_resolve_own_file("/电视剧/合集")
        self.assertEqual(result["code"], "LEAD_IN_MATERIAL_UNAVAILABLE")
        self.assertIn("文件夹", result["message"])

    def test_resolve_own_file_parent_missing(self):
        # 父目录不存在时按「素材文件不存在」提示，不能误报成服务异常
        result = self.adapter.lead_in_resolve_own_file("/电视剧/剧集01.mp4")
        self.assertEqual(result["code"], "LEAD_IN_MATERIAL_UNAVAILABLE")
        self.assertIn("素材文件不存在", result["message"])

    def test_resolve_own_file_listing_unavailable(self):
        self.transport.path_map = {
            "/电视剧": {"file_path": "/电视剧", "fid": "d2", "dir": True, "file_type": 0},
        }
        self.transport.listings["d2"] = {"code": 1, "message": "目录不可访问", "data": {"list": []}}
        result = self.adapter.lead_in_resolve_own_file("/电视剧/剧集01.mp4")
        self.assertEqual(result["code"], "LEAD_IN_FAILED")

    def test_resolve_own_file_reject_root(self):
        result = self.adapter.lead_in_resolve_own_file("/")
        self.assertEqual(result["code"], "LEAD_IN_DIR_INVALID")
        self.assertEqual(self.transport.calls, [])

    def test_resolve_own_file_reject_parent_dir(self):
        result = self.adapter.lead_in_resolve_own_file("/电视剧/../秘密.mp4")
        self.assertEqual(result["code"], "LEAD_IN_DIR_INVALID")
        self.assertEqual(self.transport.calls, [])

    def test_resolve_own_file_reject_url(self):
        result = self.adapter.lead_in_resolve_own_file("https://pan.quark.cn/s/abc")
        self.assertEqual(result["code"], "LEAD_IN_DIR_INVALID")
        self.assertEqual(self.transport.calls, [])

    def test_resolve_own_file_reject_empty(self):
        result = self.adapter.lead_in_resolve_own_file("  ")
        self.assertEqual(result["code"], "LEAD_IN_DIR_INVALID")

    # ---------- add_own_file ----------
    def test_add_own_file_ok(self):
        result = self.adapter.lead_in_add_own_file(self.material, self.target)
        self.assertEqual(result["code"], 0)
        self.assertFalse(result["data"]["sync"])
        self.assertEqual(result["data"]["handle"]["task_id"], "copy-task-1")
        self.assertEqual(result["data"]["handle"]["source"], "own_file")
        copy_calls = self.transport.calls_to("/file/copy")
        self.assertEqual(len(copy_calls), 1)
        self.assertEqual(copy_calls[0]["method"], "POST")
        self.assertEqual(copy_calls[0]["json"], {"filelist": ["f9"], "to_pdir_fid": "dir-1"})
        self.assertEqual(copy_calls[0]["params"]["pr"], "ucpro")
        self.assertEqual(copy_calls[0]["params"]["fr"], "pc")

    def test_add_own_file_conflict(self):
        self.transport.copy = {"status": 200, "code": 1, "message": "同名文件已存在"}
        result = self.adapter.lead_in_add_own_file(self.material, self.target)
        self.assertEqual(result["code"], "LEAD_IN_NAME_CONFLICT")

    def test_add_own_file_timeout(self):
        def boom(method, url, **kwargs):
            raise TimeoutError("request timeout")

        self.transport.__call__ = boom
        self.adapter._send_request = boom
        result = self.adapter.lead_in_add_own_file(self.material, self.target)
        self.assertEqual(result["code"], "LEAD_IN_TIMEOUT")

    def test_add_own_file_resolves_missing_fid(self):
        material = dict(self.material)
        material["fid"] = ""
        self.transport.path_map = {
            "/电视剧/剧集01.mp4": {"file_path": "/电视剧/剧集01.mp4", "fid": "f9", "file_type": 1},
            "/电视剧": {"file_path": "/电视剧", "fid": "d2", "dir": True, "file_type": 0},
        }
        self.transport.listings["d2"] = _quark_listing([_quark_own_file()])
        result = self.adapter.lead_in_add_own_file(material, self.target)
        self.assertEqual(result["code"], 0)
        copy_calls = self.transport.calls_to("/file/copy")
        self.assertEqual(copy_calls[0]["json"]["filelist"], ["f9"])

    def test_add_own_file_other_failure(self):
        self.transport.copy = {"status": 200, "code": 500, "message": "服务繁忙"}
        result = self.adapter.lead_in_add_own_file(self.material, self.target)
        self.assertEqual(result["code"], "LEAD_IN_FAILED")

    # ---------- verify ----------
    def test_verify_added(self):
        self.adapter.ls_dir = Mock(
            return_value=_quark_listing([_quark_own_file(fid="new-fid")])
        )
        result = self.adapter.lead_in_verify(self.material, self.target, {"task_id": "copy-task-1"})
        self.assertEqual(result["data"]["state"], "added")
        self.assertEqual(result["data"]["fid"], "new-fid")
        self.assertEqual(self.transport.calls_to("/task"), [])

    def test_verify_pending_bounded(self):
        self.transport.task = {"status": 200, "code": 0, "message": "", "data": {"status": 1}}
        self.adapter.ls_dir = Mock(return_value=_quark_listing([]))
        result = self.adapter.lead_in_verify(self.material, self.target, {"task_id": "copy-task-9"})
        self.assertEqual(result["data"]["state"], "pending")
        self.assertEqual(self.adapter.ls_dir.call_count, 3)

    def test_verify_conflict_same_name_different_size(self):
        self.adapter.ls_dir = Mock(
            return_value=_quark_listing([_quark_own_file(fid="other", size=99999, md5="zzz")])
        )
        result = self.adapter.lead_in_verify(self.material, self.target, {"task_id": "copy-task-1"})
        self.assertEqual(result["data"]["state"], "failed")
        self.assertIn("同名", result["data"]["message"])


class TestQuarkOwnBrowse(unittest.TestCase):
    """夸克「本账号目录浏览」"""

    def setUp(self):
        self.transport = FakeQuarkTransport()
        self.adapter = QuarkAdapter(cookie="__puus=test")
        self.adapter._send_request = self.transport

    def test_capability_browse_supported(self):
        capability = self.adapter.lead_in_capability()
        self.assertTrue(QuarkAdapter.SUPPORTS_LEAD_IN_OWN_BROWSE)
        self.assertTrue(capability["own_file_browse_supported"])

    def test_browse_root_ok(self):
        self.transport.listings["0"] = _quark_listing(
            [
                _quark_own_file(fid="f2", name="b.mp4", size=2048),
                _quark_own_file(fid="d2", name="A文件夹", size=0, file_type=0),
            ]
        )
        result = self.adapter.lead_in_list_own_dir("/")
        self.assertEqual(result["code"], 0)
        data = result["data"]
        self.assertEqual(data["path"], "/")
        self.assertEqual(data["fid"], "0")
        self.assertEqual([entry["name"] for entry in data["entries"]], ["A文件夹", "b.mp4"])
        folder, file_entry = data["entries"]
        self.assertTrue(folder["dir"])
        self.assertEqual(folder["size"], 0)
        self.assertEqual(folder["path"], "/A文件夹")
        self.assertFalse(file_entry["dir"])
        self.assertEqual(file_entry["fid"], "f2")
        self.assertEqual(file_entry["size"], 2048)
        self.assertEqual(file_entry["path"], "/b.mp4")
        # 根目录不查 path_list，只按 fid=0 列目录
        self.assertEqual(self.transport.calls_to("/file/info/path_list"), [])
        self.assertEqual(self.transport.calls_to("/file/sort")[0]["params"]["pdir_fid"], "0")

    def test_browse_subdir_ok(self):
        self.transport.path_map = {
            "/素材": {"file_path": "/素材", "fid": "d1", "dir": True, "file_type": 0}
        }
        self.transport.listings["d1"] = _quark_listing(
            [_quark_own_file(fid="f7", name="剧集07.mp4", size=4096)]
        )
        result = self.adapter.lead_in_list_own_dir("\\素材\\")
        self.assertEqual(result["code"], 0)
        data = result["data"]
        self.assertEqual(data["path"], "/素材")
        self.assertEqual(data["fid"], "d1")
        self.assertEqual(len(data["entries"]), 1)
        self.assertEqual(data["entries"][0]["path"], "/素材/剧集07.mp4")
        path_calls = self.transport.calls_to("/file/info/path_list")
        self.assertEqual(path_calls[0]["json"]["file_path"], ["/素材"])
        self.assertEqual(self.transport.calls_to("/file/sort")[0]["params"]["pdir_fid"], "d1")

    def test_browse_dir_not_found(self):
        result = self.adapter.lead_in_list_own_dir("/不存在")
        self.assertEqual(result["code"], "LEAD_IN_DIR_NOT_FOUND")
        self.assertIn("目录不存在", result["message"])
        self.assertEqual(self.transport.calls_to("/file/sort"), [])

    def test_browse_list_failed(self):
        self.transport.listings["0"] = {"code": 500, "message": "服务繁忙"}
        result = self.adapter.lead_in_list_own_dir("/")
        self.assertEqual(result["code"], "LEAD_IN_DIR_LIST_FAILED")
        self.assertIn("读取目录失败", result["message"])

    def test_browse_timeout(self):
        self.adapter.ls_dir = Mock(side_effect=TimeoutError("timeout"))
        result = self.adapter.lead_in_list_own_dir("/")
        self.assertEqual(result["code"], "LEAD_IN_TIMEOUT")
        self.assertIn("超时", result["message"])


    def test_browse_entries_normalized_and_sorted_with_dir(self):
        self.transport.path_map = {
            "/素材": {"file_path": "/素材", "fid": "d1", "dir": True, "file_type": 0}
        }
        self.adapter.ls_dir = Mock(
            return_value=_quark_listing(
                [
                    _quark_own_file(fid="f1", name="z.mp4", size=30),
                    _quark_own_file(fid="d2", name="B目录", size=0, file_type=0),
                    _quark_own_file(fid="f2", name="a.mp4", size=10),
                    _quark_own_file(fid="d1", name="A目录", size=0, file_type=0),
                    _quark_own_file(fid="f3", name=" 空格.mp4", size=20),
                ]
            )
        )
        result = self.adapter.lead_in_list_own_dir("/素材")
        self.assertEqual(result["code"], 0)
        entries = result["data"]["entries"]
        # 名称保留网盘返回的原值（含前导空格），排序按不区分大小写的名称
        self.assertEqual(
            [entry["name"] for entry in entries],
            ["A目录", "B目录", " 空格.mp4", "a.mp4", "z.mp4"],
        )
        self.assertEqual([entry["dir"] for entry in entries], [True, True, False, False, False])
        self.assertEqual(entries[2]["path"], "/素材/ 空格.mp4")
        self.assertEqual(entries[2]["size"], 20)

    def test_browse_truncated_to_200(self):
        items = [
            _quark_own_file(fid=f"f{index}", name=f"{index:03d}.mp4")
            for index in range(250)
        ]
        self.adapter.ls_dir = Mock(return_value=_quark_listing(items))
        result = self.adapter.lead_in_list_own_dir("/")
        self.assertEqual(result["code"], 0)
        self.assertEqual(len(result["data"]["entries"]), 200)

    def test_browse_invalid_paths(self):
        for path in (
            "",
            "素材",
            "https://pan.quark.cn/s/abc",
            "/素材/ /子目录",
            "/素材/../秘密",
        ):
            result = self.adapter.lead_in_list_own_dir(path)
            self.assertEqual(result["code"], "LEAD_IN_DIR_INVALID", path)
        self.assertEqual(self.transport.calls, [])

class FakeBaiduTransport:
    """模拟百度 PCS 目录列表与文件操作接口"""

    def __init__(self):
        self.listings = {}
        self.filemanager = {"errno": 0, "taskid": 123456}
        self.calls = []

    def __call__(self, method, url, params=None, headers=None, data=None, **kwargs):
        self.calls.append(
            {"method": method, "url": url, "params": params or {}, "data": data or {}}
        )
        if url.endswith("/api/list"):
            remotepath = (params or {}).get("dir") or "/"
            return FakeResponse(self.listings.get(remotepath, {"errno": 0, "list": []}))
        if url.endswith("/api/filemanager"):
            return FakeResponse(self.filemanager)
        raise AssertionError(f"未预期的百度接口: {url}")

    def calls_to(self, suffix: str):
        return [call for call in self.calls if call["url"].endswith(suffix)]


def _baidu_own_item(name="剧集01.mp4", isdir=0, fs_id=9001, size=1024, md5="abc123", path=None):
    return {
        "path": path or f"/电视剧/{name}",
        "server_filename": name,
        "isdir": isdir,
        "fs_id": fs_id,
        "size": size,
        "md5": md5,
    }


class TestBaiduOwnFile(unittest.TestCase):
    """百度「本账号文件」模式"""

    def setUp(self):
        self.transport = FakeBaiduTransport()
        self.adapter = BaiduAdapter(cookie="BDUSS=test; STOKEN=test")
        self.adapter.LEAD_IN_MAX_ATTEMPTS = 3
        self.adapter.LEAD_IN_POLL_INTERVAL = 0.0
        self.adapter._request = self.transport
        self.target = {"dir": {"fid": "/我的剧集", "path": "/我的剧集", "name": "我的剧集"}}
        self.material = {
            "source": "own_file",
            "fid": "9001",
            "name": "剧集01.mp4",
            "path": "/电视剧/剧集01.mp4",
            "size": 1024,
            "md5": "abc123",
        }

    # ---------- 能力声明 ----------
    def test_capability_own_file_supported(self):
        capability = self.adapter.lead_in_capability()
        self.assertTrue(BaiduAdapter.SUPPORTS_LEAD_IN_OWN_FILE)
        self.assertTrue(capability["own_file_supported"])

    # ---------- resolve_own_file ----------
    def test_resolve_own_file_ok(self):
        self.transport.listings["/电视剧"] = {"errno": 0, "list": [_baidu_own_item()]}
        result = self.adapter.lead_in_resolve_own_file("/电视剧/剧集01.mp4")
        self.assertEqual(result["code"], 0)
        file_info = result["data"]["file"]
        self.assertEqual(file_info["fid"], "9001")
        self.assertEqual(file_info["name"], "剧集01.mp4")
        self.assertEqual(file_info["path"], "/电视剧/剧集01.mp4")
        self.assertEqual(file_info["size"], 1024)
        self.assertEqual(file_info["md5"], "abc123")
        self.assertFalse(file_info["dir"])
        # 只请求父目录列表，未调用全盘搜索接口
        self.assertEqual(self.transport.calls_to("/api/search"), [])
        list_calls = self.transport.calls_to("/api/list")
        self.assertEqual(list_calls[0]["params"]["dir"], "/电视剧")

    def test_resolve_own_file_not_found(self):
        self.transport.listings["/电视剧"] = {"errno": 0, "list": [_baidu_own_item("其它.mp4")]}
        result = self.adapter.lead_in_resolve_own_file("/电视剧/剧集01.mp4")
        self.assertEqual(result["code"], "LEAD_IN_MATERIAL_UNAVAILABLE")

    def test_resolve_own_file_reject_folder(self):
        self.transport.listings["/电视剧"] = {
            "errno": 0,
            "list": [_baidu_own_item("合集", isdir=1, size=0, md5="")],
        }
        result = self.adapter.lead_in_resolve_own_file("/电视剧/合集")
        self.assertEqual(result["code"], "LEAD_IN_MATERIAL_UNAVAILABLE")
        self.assertIn("文件夹", result["message"])

    def test_resolve_own_file_parent_not_found(self):
        self.transport.listings["/电视剧"] = {"errno": -9, "list": []}
        result = self.adapter.lead_in_resolve_own_file("/电视剧/剧集01.mp4")
        self.assertEqual(result["code"], "LEAD_IN_MATERIAL_UNAVAILABLE")

    def test_resolve_own_file_auth_failed(self):
        self.transport.listings["/电视剧"] = {"errno": -6, "list": []}
        result = self.adapter.lead_in_resolve_own_file("/电视剧/剧集01.mp4")
        self.assertEqual(result["code"], "LEAD_IN_FAILED")
        self.assertIn("认证", result["message"])

    def test_resolve_own_file_reject_root(self):
        result = self.adapter.lead_in_resolve_own_file("/")
        self.assertEqual(result["code"], "LEAD_IN_DIR_INVALID")
        self.assertEqual(self.transport.calls, [])

    def test_resolve_own_file_reject_parent_dir(self):
        result = self.adapter.lead_in_resolve_own_file("/电视剧/../秘密.mp4")
        self.assertEqual(result["code"], "LEAD_IN_DIR_INVALID")
        self.assertEqual(self.transport.calls, [])

    def test_resolve_own_file_reject_url(self):
        result = self.adapter.lead_in_resolve_own_file("https://pan.baidu.com/s/1abc")
        self.assertEqual(result["code"], "LEAD_IN_DIR_INVALID")
        self.assertEqual(self.transport.calls, [])

    # ---------- add_own_file ----------
    def test_add_own_file_ok(self):
        # 模拟登录流程已缓存 bdstoken，避免现场请求 /disk/home
        self.adapter._bdstoken = "bd-token-test"
        result = self.adapter.lead_in_add_own_file(self.material, self.target)
        self.assertEqual(result["code"], 0)
        self.assertFalse(result["data"]["sync"])
        handle = result["data"]["handle"]
        self.assertEqual(handle["source"], "own_file")
        self.assertEqual(handle["task_id"], "123456")
        self.assertEqual(handle["dir_fid"], "/我的剧集")
        manager_calls = self.transport.calls_to("/api/filemanager")
        self.assertEqual(len(manager_calls), 1)
        params = manager_calls[0]["params"]
        self.assertEqual(manager_calls[0]["method"], "POST")
        self.assertEqual(params["opera"], "copy")
        self.assertEqual(params["onnest"], "fail")
        self.assertEqual(str(params["async"]), "1")
        self.assertEqual(params["newVerify"], 1)
        self.assertEqual(params["bdstoken"], "bd-token-test")
        filelist = json.loads(manager_calls[0]["data"]["filelist"])
        self.assertEqual(
            filelist,
            [
                {
                    "path": "/电视剧/剧集01.mp4",
                    "dest": "/我的剧集",
                    "newname": "剧集01.mp4",
                    "ondup": "fail",
                }
            ],
        )
        self.assertEqual(self.transport.calls_to("/api/search"), [])

    def test_add_own_file_conflict_errno(self):
        self.transport.filemanager = {"errno": 12, "errmsg": "转存文件失败"}
        result = self.adapter.lead_in_add_own_file(self.material, self.target)
        self.assertEqual(result["code"], "LEAD_IN_NAME_CONFLICT")

    def test_add_own_file_conflict_message(self):
        self.transport.filemanager = {"errno": 31061, "errmsg": "文件已存在"}
        result = self.adapter.lead_in_add_own_file(self.material, self.target)
        self.assertEqual(result["code"], "LEAD_IN_NAME_CONFLICT")

    def test_add_own_file_timeout(self):
        def boom(method, url, **kwargs):
            raise TimeoutError("request timeout")

        self.adapter._request = boom
        result = self.adapter.lead_in_add_own_file(self.material, self.target)
        self.assertEqual(result["code"], "LEAD_IN_TIMEOUT")

    def test_add_own_file_other_failure(self):
        self.transport.filemanager = {"errno": 500, "errmsg": "服务繁忙"}
        result = self.adapter.lead_in_add_own_file(self.material, self.target)
        self.assertEqual(result["code"], "LEAD_IN_FAILED")

    def test_add_own_file_missing_path(self):
        material = dict(self.material)
        material["path"] = ""
        result = self.adapter.lead_in_add_own_file(material, self.target)
        self.assertEqual(result["code"], "LEAD_IN_MATERIAL_UNAVAILABLE")

    # ---------- verify ----------
    def test_verify_added(self):
        self.adapter.ls_dir = Mock(
            return_value={
                "code": 0,
                "data": {"list": [{"fid": "/我的剧集/剧集01.mp4", "file_name": "剧集01.mp4", "size": 1024, "dir": False}]},
            }
        )
        result = self.adapter.lead_in_verify(
            self.material,
            self.target,
            {"source": "own_file", "async": True, "task_id": "123456"},
        )
        self.assertEqual(result["data"]["state"], "added")

    def test_verify_pending_bounded(self):
        self.adapter.ls_dir = Mock(
            return_value={"code": 0, "data": {"list": []}}
        )
        result = self.adapter.lead_in_verify(
            self.material,
            self.target,
            {"source": "own_file", "async": True, "task_id": "123456"},
        )
        self.assertEqual(result["data"]["state"], "pending")
        self.assertEqual(self.adapter.ls_dir.call_count, 3)


class TestBaiduOwnBrowse(unittest.TestCase):
    """百度「本账号目录浏览」"""

    def setUp(self):
        self.transport = FakeBaiduTransport()
        self.adapter = BaiduAdapter(cookie="BDUSS=test; STOKEN=test")
        self.adapter._request = self.transport

    def test_capability_browse_supported(self):
        capability = self.adapter.lead_in_capability()
        self.assertTrue(BaiduAdapter.SUPPORTS_LEAD_IN_OWN_BROWSE)
        self.assertTrue(capability["own_file_browse_supported"])

    def test_browse_root_ok(self):
        self.transport.listings["/"] = {
            "errno": 0,
            "list": [
                _baidu_own_item(
                    name="剧集01.mp4", fs_id=100, size=2048, md5="", path="/剧集01.mp4"
                ),
                _baidu_own_item(
                    name="合集", isdir=1, fs_id=101, size=0, md5="", path="/合集"
                ),
            ],
        }
        result = self.adapter.lead_in_list_own_dir("/")
        self.assertEqual(result["code"], 0)
        data = result["data"]
        self.assertEqual(data["path"], "/")
        self.assertEqual(data["fid"], "0")
        self.assertEqual(
            [entry["name"] for entry in data["entries"]], ["合集", "剧集01.mp4"]
        )
        folder, file_entry = data["entries"]
        self.assertTrue(folder["dir"])
        self.assertEqual(folder["path"], "/合集")
        self.assertEqual(folder["size"], 0)
        self.assertFalse(file_entry["dir"])
        self.assertEqual(file_entry["path"], "/剧集01.mp4")
        self.assertEqual(file_entry["size"], 2048)
        self.assertEqual(self.transport.calls_to("/api/search"), [])
        self.assertEqual(self.transport.calls_to("/api/list")[0]["params"]["dir"], "/")

    def test_browse_subdir_ok(self):
        self.transport.listings["/"] = {
            "errno": 0,
            "list": [
                _baidu_own_item(name="素材", isdir=1, fs_id=200, size=0, path="/素材")
            ],
        }
        self.transport.listings["/素材"] = {
            "errno": 0,
            "list": [
                _baidu_own_item(
                    name="剧集02.mp4",
                    fs_id=201,
                    size=4096,
                    path="/素材/剧集02.mp4",
                )
            ],
        }
        result = self.adapter.lead_in_list_own_dir("/素材")
        self.assertEqual(result["code"], 0)
        data = result["data"]
        self.assertEqual(data["path"], "/素材")
        self.assertEqual(data["fid"], "/素材")
        self.assertEqual(len(data["entries"]), 1)
        self.assertEqual(data["entries"][0]["fid"], "/素材/剧集02.mp4")
        self.assertEqual(data["entries"][0]["path"], "/素材/剧集02.mp4")
        list_dirs = [call["params"].get("dir") for call in self.transport.calls_to("/api/list")]
        self.assertEqual(list_dirs, ["/", "/素材"])

    def test_browse_dir_not_found(self):
        result = self.adapter.lead_in_list_own_dir("/素材")
        self.assertEqual(result["code"], "LEAD_IN_DIR_NOT_FOUND")
        self.assertIn("目录不存在", result["message"])

    def test_browse_list_failed(self):
        self.transport.listings["/"] = {"errno": -6, "errmsg": "认证失败"}
        result = self.adapter.lead_in_list_own_dir("/")
        self.assertEqual(result["code"], "LEAD_IN_DIR_LIST_FAILED")
        self.assertIn("读取目录失败", result["message"])


class TestBrowseUnsupported(unittest.TestCase):
    """未声明浏览能力的适配器走基类默认实现"""

    def test_base_default_unsupported(self):
        dummy = SimpleNamespace(DRIVE_TYPE="base", DRIVE_NAME="测试网盘")
        result = BaseCloudDriveAdapter.lead_in_list_own_dir(dummy, "/")
        self.assertEqual(result["code"], "LEAD_IN_UNSUPPORTED")
        self.assertIn("暂不支持浏览本账号目录", result["message"])
        capability = BaseCloudDriveAdapter.lead_in_capability(dummy)
        self.assertFalse(capability["own_file_browse_supported"])

if __name__ == "__main__":
    unittest.main()
