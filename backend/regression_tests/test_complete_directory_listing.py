"""Strict enumeration uses mocked APIs and never a real database."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from app.infrastructure.drive.adapters.base_adapter import BaseCloudDriveAdapter
from app.infrastructure.drive.adapters.uc_adapter import UCAdapter


def item(i):
    return {"fid": str(i), "file_name": f"episode-{i}.mp4", "dir": False}


def page(items, total=None):
    result = {"code": 0, "data": {"list": items}}
    if total is not None:
        result["metadata"] = {"_total": total}
    return result


def adapter(pages):
    obj = object.__new__(UCAdapter)
    obj._send_request = Mock(side_effect=[SimpleNamespace(json=lambda p=p: p) for p in pages])
    return obj


def test_default_requires_explicit_opt_in():
    class Unknown(UCAdapter):
        pass
    obj = object.__new__(Unknown)
    obj.ls_dir = Mock(return_value=page([]))
    assert BaseCloudDriveAdapter.SUPPORTS_COMPLETE_LISTING is False
    assert obj.ls_dir_complete("0")["code"] != 0
    obj.ls_dir.assert_not_called()


def test_complete_multiple_pages_and_legacy_unchanged():
    obj = adapter([page([item(1)], 2), page([item(2)], 2)])
    result = obj.ls_dir_complete("0")
    assert result["code"] == 0, result
    assert len(result["data"]["list"]) == 2
    assert obj._send_request.call_args_list[1].kwargs["params"]["_page"] == 2
    legacy = adapter([page([item(1)])])
    assert legacy.ls_dir("0")["code"] == 0
    assert legacy._send_request.call_count == 1


@pytest.mark.parametrize("pages", [
    [page([item(1)], 2), {"code": 1}],
    [page([item(1)], 2), page([item(1)], 2)],
    [page([item(1)])],
    [page([item(1)], 2), page([], 2)],
    [page([item(1)], 2), page([item(2)], 3)],
    [page([None], 1)],
    [page([{"fid": "1"}], 1)],
    [{"code": 0, "status": 500, "data": {"list": []}, "metadata": {"_total": 0}}],
])
def test_rejects_incomplete_or_ambiguous(pages):
    result = adapter(pages).ls_dir_complete("0")
    assert result["code"] != 0
    assert result["data"]["list"] == []


def test_page_cap_is_error():
    obj = adapter([page([item(1)], 2)])
    obj.COMPLETE_LISTING_MAX_PAGES = 1
    assert obj.ls_dir_complete("0")["code"] != 0


def driver(kind, mode="ok"):
    from app.infrastructure.drive.adapters.baidu_adapter import BaiduAdapter
    from app.infrastructure.drive.adapters.pan123_adapter import Pan123Adapter
    from app.infrastructure.drive.adapters.aliyun_adapter import AliyunAdapter
    from app.infrastructure.drive.adapters.xunlei_adapter import XunleiAdapter
    from app.infrastructure.drive.adapters.cloud115_adapter import Cloud115Adapter
    from app.infrastructure.drive.adapters.cloud139_adapter import Cloud139Adapter
    from app.infrastructure.drive.adapters.cloud189_adapter import Cloud189Adapter
    from app.infrastructure.drive.adapters.guangya_adapter import GuangyaAdapter
    from app.infrastructure.drive.adapters.quark_adapter import QuarkAdapter
    classes = {"baidu": BaiduAdapter, "123pan": Pan123Adapter, "aliyun": AliyunAdapter, "xunlei": XunleiAdapter, "115": Cloud115Adapter, "cloud139": Cloud139Adapter, "cloud189": Cloud189Adapter, "guangya": GuangyaAdapter, "quark": QuarkAdapter, "uc": UCAdapter}
    a = object.__new__(classes[kind])
    a._ensure_login = Mock(return_value=True)
    a._ensure_token_valid = Mock(return_value=True)
    a._ensure_tokens_valid = Mock(return_value=True)
    a._token = SimpleNamespace(default_drive_id="drive")
    a._session = True
    a.auth_session = object()
    a._resolve_fid_to_path = Mock(return_value="/shows")
    a._API_LIST_PAGE_SIZE = 1
    a._check_response = lambda p: p
    a._map_root_fid = lambda p: p
    if kind == "115":
        a._safe_json = lambda p: p
    if kind == "baidu":
        pages = [{"errno": 0, "list": [{"path": "/shows/a", "fs_id": "1"}]}, {"errno": 0, "list": [{"path": "/shows/b", "fs_id": "2"}]}, {"errno": 0, "list": []}]
    elif kind == "123pan":
        pages = [{"code": 0, "data": {"InfoList": [{"FileId": 1, "FileName": "a"}], "Next": "next"}}, {"code": 0, "data": {"InfoList": [{"FileId": 2, "FileName": "b"}], "Next": -1}}]
    elif kind == "aliyun":
        pages = [{"items": [{"file_id": "1", "name": "a"}], "next_marker": "next"}, {"items": [{"file_id": "2", "name": "b"}], "next_marker": ""}]
    elif kind == "xunlei":
        pages = [{"files": [{"id": "1", "name": "a"}], "next_page_token": "next"}, {"files": [{"id": "2", "name": "b"}], "next_page_token": ""}]
    elif kind == "115":
        pages = [{"state": True, "data": [{"fid": "1", "n": "a", "t": "0"}], "count": 2}, {"state": True, "data": [{"cid": "2", "n": "b", "t": "0"}], "count": 2}]
    elif kind == "cloud139":
        pages = [{"items": [{"fileId": "1", "name": "a"}], "nextPageCursor": "next"}, {"items": [{"fileId": "2", "name": "b"}], "nextPageCursor": None}]
    elif kind == "cloud189":
        pages = [{"fileListAO": {"folderList": [], "fileList": [{"id": "1", "name": "a"}], "count": 2}}, {"fileListAO": {"folderList": [], "fileList": [{"id": "2", "name": "b"}], "count": 2}}]
    elif kind == "guangya":
        pages = [{"code": 0, "data": {"list": [{"id": "1", "name": "a"}], "total": 2}}, {"code": 0, "data": {"list": [{"id": "2", "name": "b"}], "total": 2}}]
    else:
        pages = [page([item(1)], 2), page([item(2)], 2)]
    if mode == "error":
        pages[1] = {"code": 7, "errno": 7, "state": False, "error": "later page failed"}
    if mode == "malformed":
        pages[1] = {"code": 0}
    if mode == "duplicate":
        pages[1] = pages[0]
    request = Mock(side_effect=pages)
    a._api_list_page = request
    a._list_disk_dir = request
    a._request_json = request
    a._request = request
    a._client = SimpleNamespace(fs_files=request)
    a._send_request = Mock(side_effect=[SimpleNamespace(json=lambda p=p: p) for p in pages])
    return a


KINDS = ["baidu", "123pan", "aliyun", "xunlei", "115", "cloud139", "cloud189", "guangya", "quark", "uc"]


@pytest.mark.parametrize("kind", KINDS)
def test_all_audited_drivers_enumerate_multiple_pages(kind):
    a = driver(kind)
    result = a.ls_dir_complete("0")
    assert result["code"] == 0, result
    assert len(result["data"]["list"]) == 2
    if kind == "123pan":
        params = a._request.call_args_list[1].kwargs["params"]
        assert str(params["Page"]) == "2"
        assert params["next"] == "next"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("mode", ["error", "malformed", "duplicate", "cap"])
def test_all_audited_drivers_fail_closed(kind, mode):
    a = driver(kind, mode)
    if mode == "cap":
        a.COMPLETE_LISTING_MAX_PAGES = 1
    result = a.ls_dir_complete("0")
    assert result["code"] != 0, result
    assert result["data"]["list"] == []


def test_cloud189_portal_root_resolves_logical_savepath():
    from app.infrastructure.drive.saved_progress import SavedProgressScanner
    a = driver("cloud189")
    a._map_root_fid = lambda p: "-11" if p == "0" else p
    a._request_json.side_effect = [
        {"data": [{"fileId": "shows", "fileName": "Shows", "isFolder": True}], "recordCount": 1, "pageSize": 60, "pageNum": 1},
        {"fileListAO": {"folderList": [], "fileList": [], "count": 0}},
    ]
    plan = SimpleNamespace(drive=SimpleNamespace(savepath="/Shows"), task_data={"addition": {}, "extra": {}, "taskname": "Show", "pattern": "", "replace": "", "magic_regex": {}})
    result = SavedProgressScanner(adapter_factory=lambda p: a).scan(plan)
    assert result.saved_latest.episode == 0
    assert result.scanned_directories == 1
    root_call, child_call = a._request_json.call_args_list
    assert root_call.args[1].endswith("/api/portal/listFiles.action")
    assert child_call.kwargs["params"]["folderId"] == "shows"


@pytest.mark.parametrize("payload", [
    {"data": [], "recordCount": 1, "pageSize": 60, "pageNum": 1},
    {"data": []},
    {"data": [], "recordCount": 0, "pageSize": 60, "pageNum": 2},
    {"data": [{"fileId": "shows", "fileName": "Shows"}], "recordCount": 1, "pageSize": 0, "pageNum": 1},
    {"data": {}, "recordCount": 0},
    {"data": [None], "recordCount": 1},
    {"data": [], "recordCount": True},
])
def test_cloud189_portal_root_partial_or_ambiguous_fails(payload):
    a = driver("cloud189")
    a._map_root_fid = lambda p: "-11"
    a._request_json.side_effect = [payload]
    result = a.ls_dir_complete("0")
    assert result["code"] != 0
    assert result["data"]["list"] == []


@pytest.mark.parametrize("kind,field", [("123pan", "Next"), ("aliyun", "next_marker"), ("xunlei", "next_page_token"), ("cloud139", "nextPageCursor")])
def test_missing_cursor_is_not_completion(kind, field):
    a = driver(kind)
    # All mocked raw APIs share the same Mock; replace it with a malformed terminal page.
    if kind == "123pan":
        malformed = {"code": 0, "data": {"InfoList": []}}
    else:
        malformed = {"items" if kind in ("aliyun", "cloud139") else "files": []}
    a._request.side_effect = [malformed]
    assert a.ls_dir_complete("0")["code"] != 0


def test_baidu_numeric_id_cannot_fall_back_to_root():
    a = driver("baidu")
    result = a.ls_dir_complete("123456")
    assert result["code"] != 0
    a._resolve_fid_to_path.assert_not_called()
    a._api_list_page.assert_not_called()


@pytest.mark.parametrize("result", [
    None,
    {"code": 0},
    {"code": 0, "data": {"list": {}}},
    {"code": 0, "data": {"list": [None]}},
    {"code": 0, "data": {"list": [item(1), item(1)]}},
    {"code": 0, "data": {"list": [{"fid": "1"}]}},
    {"code": 0, "data": {"list": [{"fid": None, "file_name": "a"}]}},
    {"code": False, "data": {"list": []}},
    {"code": 0, "status": 500, "data": {"list": []}},
    {"code": 0, "data": {"list": []}, "metadata": {"_total": 1}},
    {"code": 0, "data": {"list": []}, "total": 1},
    {"code": 0, "data": {"list": [], "total": 1}},
])
def test_base_validator_rejects_invalid_contract(result):
    class Audited(UCAdapter):
        SUPPORTS_COMPLETE_LISTING = True
    a = object.__new__(Audited)
    a.ls_dir = Mock(return_value=result)
    response = a.ls_dir_complete("0")
    assert response["code"] != 0
    assert response["data"]["list"] == []
    a.ls_dir.assert_called_once_with("0", max_items=0, require_complete=True)


def test_base_validator_accepts_driver_id_name_aliases():
    from app.infrastructure.drive.adapters.complete_listing import validate_result
    result = {"code": 0, "data": {"list": [{"file_id": "1", "name": "a"}, {"cid": "2", "n": "folder"}]}, "metadata": {"_total": 2}}
    assert validate_result(result) is result


@pytest.mark.parametrize("kind,key", [("123pan", "Next"), ("aliyun", "next_marker"), ("xunlei", "next_page_token"), ("cloud139", "nextPageCursor")])
def test_repeated_cursor_with_distinct_items_fails(kind, key):
    a = driver(kind)
    pages = list(a._request.side_effect)
    first = pages[0]["data"] if kind == "123pan" else pages[0]
    second = pages[1]["data"] if kind == "123pan" else pages[1]
    second[key] = first[key]
    a._request.side_effect = pages
    result = a.ls_dir_complete("0")
    assert result["code"] != 0
    assert "Repeated next-page cursor" in result["message"]
    assert result["data"]["list"] == []
