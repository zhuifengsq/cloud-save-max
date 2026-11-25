import pytest

from app.infrastructure.drive.adapters.aliyun_adapter import AliyunAdapter
from app.infrastructure.drive.adapters.cloud115_adapter import Cloud115Adapter
from app.infrastructure.drive.adapters.pan123_adapter import Pan123Adapter
from app.infrastructure.drive.adapters.xunlei_adapter import XunleiAdapter


@pytest.mark.parametrize("provider", ["123pan", "aliyun", "xunlei"])
@pytest.mark.parametrize("outcome", ["complete", "empty", "malformed", "repeated_cursor"])
def test_share_cursor_read_requires_complete_page_payload(provider, outcome):
    classes = {"123pan": Pan123Adapter, "aliyun": AliyunAdapter, "xunlei": XunleiAdapter}
    adapter = object.__new__(classes[provider])
    adapter._share_base = "https://example.test"
    adapter._debug = False
    adapter._check_response = lambda response: response

    def page(fid, cursor):
        if provider == "123pan":
            items = [{"FileId": fid, "FileName": f"E{fid}.mp4"}] if fid else []
            return {"code": 0, "data": {"InfoList": items, "Next": -1 if cursor == "" else cursor}}
        if provider == "aliyun":
            items = [{"file_id": fid, "name": f"E{fid}.mp4"}] if fid else []
            return {"items": items, "next_marker": cursor}
        items = [{"id": fid, "name": f"E{fid}.mp4"}] if fid else []
        return {"files": items, "next_page_token": cursor}

    second = {
        "complete": page("2", ""), "malformed": {"code": 0},
        "repeated_cursor": page("2", "next"),
    }.get(outcome)
    pages = [page("", "")] if outcome == "empty" else [page("1", "next"), second, page("3", "")]
    requested = []

    def request(*_args, **kwargs):
        sent = kwargs.get("params", kwargs.get("body"))
        requested.append(sent.get("next", sent.get("marker", sent.get("page_token"))))
        return pages[len(requested) - 1]

    adapter._request = request
    result = adapter.get_detail("share", "token", "root")
    assert len(requested) == (1 if outcome == "empty" else 2)
    if outcome in {"complete", "empty"}:
        assert result["code"] == 0
        assert [item["fid"] for item in result["data"]["list"]] == (["1", "2"] if outcome == "complete" else [])
    else:
        assert result["code"] != 0
        assert result["data"]["list"] == []


@pytest.mark.parametrize("second_page", [[], [{"fid": "2", "n": "E02.mp4"}]])
def test_cloud115_honors_declared_total_after_short_page(second_page):
    adapter = object.__new__(Cloud115Adapter)
    adapter._create_share_session = lambda *_: object()
    adapter._safe_json = lambda response: response
    adapter._get_category = lambda _: "video"
    calls = []

    def request(_session, _method, url, **kwargs):
        calls.append(url)
        items = [{"fid": "1", "n": "E01.mp4"}] if len(calls) == 1 else second_page
        return {"state": True, "data": {"list": items, "count": 2}}

    adapter._request = request
    result = adapter.get_detail("share", "token", "0")
    # A declared remainder on a short offset page is ambiguous because advancing
    # by the normal page limit could skip files.
    assert len(calls) == 1
    assert result["code"] != 0
    assert result["data"]["list"] == []
