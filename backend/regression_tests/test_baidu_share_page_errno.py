"""百度分享页 errno 透传护栏：死链被判「有效」的回归。

背景（真实事故）：`https://pan.baidu.com/s/1liYN70njkafHvRKCyXZNIA?pwd=6666` 在百度侧已被删除，
但「生成分享检测」一直显示「有效」。链路是两层漏判：

1. 百度 `share/verify` 对已删除的分享也返回 `errno=0`，所以 `get_stoken` 认为成功；
2. 真正的失效信号在分享页 JSON 里（`share_page_type=error` + `errno=-7` + `file_list=[]`），
   而 `get_detail` 只看 `file_list`，空列表时直接返回 `code=0`，把 errno 丢掉了。

本文件锁死修复后的语义：`get_detail` 必须透传分享页错误、`save_file` 必须拒绝失效分享、
端口层 `probe_share` 必须判 invalid。全程 monkeypatch，不联网。
"""
from app.infrastructure.drive.adapters.baidu_adapter import BaiduAdapter
from app.infrastructure.drive.gateway import gateway_for_adapter

_DEAD_PAGE = {"errno": -7, "share_page_type": "error", "file_list": []}
_OK_EMPTY_PAGE = {"errno": 0, "file_list": []}


def _adapter(monkeypatch, shared_page, *, token=None):
    adapter = BaiduAdapter(cookie="BDUSS=fake-bduss; STOKEN=fake-stoken")
    monkeypatch.setattr(
        adapter, "_api_access_shared", lambda *args, **kwargs: {"errno": 0, "err_msg": ""}
    )
    monkeypatch.setattr(adapter, "_api_shared_paths", lambda *args, **kwargs: dict(shared_page))
    monkeypatch.setattr(adapter, "_resolve_fid_to_path", lambda *args, **kwargs: "/")
    monkeypatch.setattr(adapter, "ls_dir", lambda *args, **kwargs: {"code": 0, "data": {"list": []}})
    if token is not None:
        monkeypatch.setattr(adapter, "get_stoken", lambda *args, **kwargs: dict(token))
    return adapter


def test_get_detail_propagates_share_page_errno(monkeypatch):
    adapter = _adapter(monkeypatch, _DEAD_PAGE)
    detail = adapter.get_detail("1liYN70njkafHvRKCyXZNIA", "1liYN70njkafHvRKCyXZNIA:6666", "0")
    assert detail["code"] == -7, detail
    assert "不存在" in detail["message"] or "取消" in detail["message"], detail
    assert detail["data"]["list"] == []


def test_get_detail_treats_share_page_type_error_as_invalid(monkeypatch):
    adapter = _adapter(monkeypatch, {"share_page_type": "error", "file_list": []})
    detail = adapter.get_detail("abc", "abc:6666", "0")
    assert detail["code"] != 0
    assert detail["data"]["list"] == []


def test_get_detail_keeps_normal_page_success(monkeypatch):
    """没有失效信号的空目录仍走原来的 code=0（是否可用由端口层判定，别在这里误杀）。"""
    adapter = _adapter(monkeypatch, _OK_EMPTY_PAGE)
    detail = adapter.get_detail("abc", "abc:6666", "0")
    assert detail["code"] == 0


def test_save_file_refuses_dead_share(monkeypatch):
    adapter = _adapter(monkeypatch, _DEAD_PAGE)
    result = adapter.save_file(["123"], ["123"], "0", "abc", "abc:6666", file_names=["x.mp4"])
    assert result["code"] != 0, result
    assert "不存在" in result["message"] or "取消" in result["message"], result


def test_gateway_probe_marks_dead_baidu_share_invalid(monkeypatch):
    """用户可见的验收点：检测「生成分享」时死链必须显示已失效，而不是有效。"""
    adapter = _adapter(
        monkeypatch,
        _DEAD_PAGE,
        token={"status": 200, "code": 0, "data": {"stoken": "abc:6666"}},
    )
    result = gateway_for_adapter(adapter, drive_type="baidu").probe_share(
        "https://pan.baidu.com/s/abc", passcode="6666"
    )
    assert result.valid is False
    assert result.kind == "invalid"
    assert result.stage == "read"
    assert result.message
