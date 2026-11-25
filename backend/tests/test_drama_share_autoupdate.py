from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import pytest

from app.application import drama_share_autoupdate, share_preview_batch
from app.application.drama_share_autoupdate import resolve_drama_shareurl_update
from app.application.share_listing import ShareListingReader
from app.infrastructure.drive.adapters.baidu_adapter import BaiduAdapter
from app.infrastructure.drive.adapters.cloud139_adapter import Cloud139Adapter
from app.infrastructure.drive.adapters.quark_adapter import QuarkAdapter
from app.infrastructure.execution.drama_plan import build_transfer_plan


def _task(**overrides):
    task = SimpleNamespace(
        id=1,
        task_type="drama",
        taskname="测试剧",
        shareurl="https://pan.quark.cn/s/oldshare",
        savepath="/剧集/测试剧",
        account_name="",
        tmdb_id=0,
        tmdb_media_type=None,
        extra_json=json.dumps({"auto_update_shareurl": True}),
        addition_json=json.dumps({}),
    )
    for key, value in overrides.items():
        setattr(task, key, value)
    return task


class DramaShareAutoUpdateTests(unittest.TestCase):
    def test_linked_task_searches_by_task_name(self) -> None:
        task = _task(taskname="  斗罗大陆  ", tmdb_id=228429, tmdb_media_type="tv")
        with (
            patch(
                "app.application.drama_share_autoupdate.get_tmdb_detail_cached",
                return_value=(True, {"name": "斗罗大陆Ⅱ绝世唐门", "original_name": "斗罗大陆Ⅱ绝世唐门"}, [], [], None),
                create=True,
            ),
            patch("app.application.drama_share_autoupdate.fetch_task_suggestions", return_value=([], False, None)) as search,
        ):
            result = resolve_drama_shareurl_update(Mock(), task)

        self.assertEqual(result["reason"], "no_candidates")
        self.assertEqual([call.kwargs["keyword"] for call in search.call_args_list], ["斗罗大陆"])

    def test_repairs_dead_link_and_keeps_folder_for_normal_mode(self) -> None:
        task = _task()
        suggestion = {"shareurl": "https://pan.quark.cn/s/newshare", "taskname": "测试剧", "datetime": "2026-10-27 12:00:00"}
        groups = [([{"file_name": "08.mp4", "updated_at": 100}], "fid-new", 100, "4k")]
        db = Mock()
        with (
            patch("app.application.drama_share_autoupdate._search_candidates", return_value=([suggestion], False, {})),
            patch("app.application.drama_share_autoupdate._prepare_candidates", return_value=([suggestion], {})),
            patch("app.application.share_preview_batch.fetch_share_file_list_grouped", return_value=groups),
        ):
            result = resolve_drama_shareurl_update(db, task)

        self.assertTrue(result["updated"])
        self.assertEqual(task.shareurl, "https://pan.quark.cn/s/newshare#/list/share/fid-new")
        db.flush.assert_called_once()

    def test_folder_diff_mode_keeps_share_root(self) -> None:
        task = _task(addition_json=json.dumps({"folder_mode": "folder_diff"}))
        suggestion = {"shareurl": "https://pan.quark.cn/s/newshare", "taskname": "测试剧"}
        groups = [([{"file_name": "08.mp4", "updated_at": 100}], "fid-new", 100, "4k")]
        db = Mock()
        with (
            patch("app.application.drama_share_autoupdate._search_candidates", return_value=([suggestion], False, {})),
            patch("app.application.drama_share_autoupdate._prepare_candidates", return_value=([suggestion], {})),
            patch("app.application.share_preview_batch.fetch_share_file_list_grouped", return_value=groups),
        ):
            result = resolve_drama_shareurl_update(db, task)

        self.assertTrue(result["updated"])
        self.assertEqual(task.shareurl, "https://pan.quark.cn/s/newshare")

    def test_no_previewable_candidate_keeps_old_link(self) -> None:
        task = _task()
        suggestion = {"shareurl": "https://pan.quark.cn/s/newshare", "taskname": "测试剧"}
        db = Mock()
        with (
            patch("app.application.drama_share_autoupdate._search_candidates", return_value=([suggestion], False, {})),
            patch("app.application.drama_share_autoupdate._prepare_candidates", return_value=([suggestion], {})),
            patch("app.application.share_preview_batch.fetch_share_file_list_grouped", return_value=[]),
        ):
            result = resolve_drama_shareurl_update(db, task)

        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "no_better_candidate")
        self.assertEqual(task.shareurl, "https://pan.quark.cn/s/oldshare")

    def test_disabled_toggle_skips(self) -> None:
        task = _task(extra_json=json.dumps({"auto_update_shareurl": False}))
        result = resolve_drama_shareurl_update(Mock(), task)
        self.assertFalse(result["checked"])
        self.assertEqual(result["reason"], "not_applicable")

    def test_same_link_is_not_applied(self) -> None:
        task = _task(shareurl="https://pan.quark.cn/s/newshare")
        suggestion = {"shareurl": "https://pan.quark.cn/s/newshare", "taskname": "测试剧"}
        groups = [([{"file_name": "08.mp4", "updated_at": 100}], "fid-new", 100, "4k")]
        with (
            patch("app.application.drama_share_autoupdate._search_candidates", return_value=([suggestion], False, {})),
            patch("app.application.drama_share_autoupdate._prepare_candidates", return_value=([suggestion], {})),
            patch("app.application.share_preview_batch.fetch_share_file_list_grouped", return_value=groups),
        ):
            result = resolve_drama_shareurl_update(Mock(), task)

        self.assertFalse(result["updated"])
        self.assertEqual(result["reason"], "no_better_candidate")


def _listed_file(fid, name, updated_at=0):
    return {"fid": fid, "file_name": name, "updated_at": updated_at}


def _listed_dir(fid, name):
    return {"fid": fid, "file_name": name, "dir": True}


def _probe_listing(monkeypatch, adapter, old, candidate, rows, addition):
    task = _task(shareurl=old, addition_json=json.dumps(addition))
    expected_share, expected_pwd, _, _ = adapter.extract_url(candidate)
    reads = []

    def get_detail(share, token, fid):
        assert (share, token) == (expected_share, "token")
        path = str(fid or "")
        reads.append(path)
        items = rows[path]
        return {"code": 0, "data": {"list": items}, "metadata": {"_total": len(items)}}

    def listing(db, url, account_name, release_read):
        share, pwd, fid, _ = adapter.extract_url(url)
        assert pwd == expected_pwd
        return ShareListingReader(adapter, share, "token", fid)

    suggestion = {"shareurl": candidate, "taskname": task.taskname}
    monkeypatch.setattr(adapter, "get_detail", get_detail)
    monkeypatch.setattr(share_preview_batch, "_prepare_listing", listing)
    monkeypatch.setattr(drama_share_autoupdate, "_search_candidates", lambda *a, **kw: ([suggestion], False, {}))
    monkeypatch.setattr(drama_share_autoupdate, "_prepare_candidates", lambda *a, **kw: ([suggestion], {}))
    result = resolve_drama_shareurl_update(Mock(), task)
    return adapter, task, result, reads


def _probe_quark(monkeypatch, rows, addition, fragment=""):
    return _probe_listing(
        monkeypatch, object.__new__(QuarkAdapter), "https://pan.quark.cn/s/oldshare",
        "https://pan.quark.cn/s/newshare?pwd=code" + fragment, rows, addition,
    )


@pytest.mark.parametrize("has_eligible", [True, False])
@pytest.mark.parametrize("nested", [False, True])
def test_folder_diff_chooses_only_a_branch_with_eligible_files(monkeypatch, has_eligible, nested):
    trailer, show, season = "a" * 32, "b" * 32, "c" * 32
    name = "4K 正片" if has_eligible else "普通正片"
    episode = _listed_file("episode", "Show.S01E01.mkv")
    rows = {
        "": [_listed_dir(trailer, "预告"), _listed_dir(show, "归档" if nested else name)],
        trailer: [_listed_file("clip", "preview.mkv")],
        show: [_listed_dir(season, name)] if nested else [episode],
        season: [episode],
    }
    adapter, task, result, reads = _probe_quark(
        monkeypatch, rows, {"folder_mode": "folder_diff", "folder_filter": "4K", "folder_exclude": "预告"},
    )
    assert result["updated"] is has_eligible
    assert reads == (["", show, season] if nested else ["", show])
    if has_eligible:
        assert adapter.extract_url(task.shareurl)[:3] == ("newshare", "code", show)
    else:
        assert task.shareurl == "https://pan.quark.cn/s/oldshare"


def test_quark_root_files_can_be_selected_and_enter_normal_plan(monkeypatch):
    episodes = [_listed_file("e1", "Show.S01E01.mkv"), _listed_file("e2", "Show.S01E02.mkv")]
    adapter, task, result, reads = _probe_quark(monkeypatch, {"": episodes}, {})
    assert result["updated"]
    assert adapter.extract_url(task.shareurl)[:3] == ("newshare", "code", 0)
    assert reads == [""]
    files = adapter.get_detail("newshare", "token", 0)["data"]["list"]
    plan = build_transfer_plan({}, filter_files=list, share_files=files, dest_file_list=[])
    assert [item.fid for item in plan] == ["e1", "e2"]


def test_reader_keeps_root_and_child_groups_and_embedded_fid(monkeypatch):
    child = "c" * 32
    rows = {
        "": [_listed_file("root", "Show.S01E01.mkv"), _listed_dir(child, "4K")],
        child: [_listed_file("child", "Show.S01E02.mkv")],
    }
    adapter, _, _, _ = _probe_quark(monkeypatch, rows, {})
    groups = ShareListingReader(adapter, "newshare", "token", 0).groups()
    assert {(group[1], group[0][0]["file_name"]) for group in groups} == {
        ("", "Show.S01E01.mkv"), (child, "Show.S01E02.mkv"),
    }
    fragment = f"#/list/share/{child}-4K"
    adapter, task, result, reads = _probe_quark(monkeypatch, rows, {"folder_mode": "folder_diff"}, fragment)
    assert result["updated"]
    assert adapter.extract_url(task.shareurl)[:3] == ("newshare", "code", child)
    assert task.shareurl.endswith(fragment)
    assert reads == [child]


def test_incomplete_quark_listing_is_not_a_candidate():
    adapter = SimpleNamespace(get_detail=lambda *args: {
        "code": 0,
        "data": {"list": [_listed_file("e1", "Show.S01E01.mkv")]},
        "metadata": {"_total": 2, "_listing_complete": False},
    })
    assert ShareListingReader(adapter, "newshare", "token", 0).groups() == []


@pytest.mark.parametrize("priority,mode,winner", [
    ("4K", "", "high"), ("HDR|4K", "any", "high"), ("HDR|4K", "all", "many"), ("", "", "many"),
])
def test_normal_mode_prefers_matching_priority_over_file_count(monkeypatch, priority, mode, winner):
    high, many = "a" * 32, "b" * 32
    rows = {
        "": [_listed_dir(many, "普通"), _listed_dir(high, "4K")],
        high: [_listed_file("episode", "Show.S01E01.mkv", 10)],
        many: [_listed_file(str(i), f"clip{i}.mkv", 100) for i in range(6)],
    }
    adapter, task, result, _ = _probe_quark(monkeypatch, rows, {
        "folder_priority": priority, "folder_priority_mode": mode,
    })
    assert result["updated"]
    assert adapter.extract_url(task.shareurl)[:3] == ("newshare", "code", high if winner == "high" else many)


def test_normal_mode_uses_file_update_time_to_break_count_ties(monkeypatch):
    older, newer = "a" * 32, "b" * 32
    rows = {
        "": [_listed_dir(older, "普通"), _listed_dir(newer, "4K")],
        older: [_listed_file("old", "Show.S01E01.mkv", 10)],
        newer: [_listed_file("new", "Show.S01E02.mkv", 20)],
    }
    adapter, task, result, _ = _probe_quark(monkeypatch, rows, {})
    assert result["updated"]
    assert adapter.extract_url(task.shareurl)[:3] == ("newshare", "code", newer)


@pytest.mark.parametrize("url, share_id", [
    ("https://pan.baidu.com/s/1Test?source=app&flag=#Q2w9", "1Test"),
    ("https://pan.baidu.com/share/init?surl=Test&pwd=Q2w9&source=app&flag=", "1Test"),
    ("https://pan.baidu.com/s/1Test?source=app&flag=#/list/share/12?pwd=Q2w9", "1Test"),
])
def test_baidu_rewrite_preserves_share_id_and_passcode(url, share_id):
    adapter = object.__new__(BaiduAdapter)
    assert adapter.extract_url(url)[:2] == (share_id, "Q2w9")
    for target in ("123", "456", None):
        url = drama_share_autoupdate._rewrite_shareurl_with_fid(url, target)
        assert adapter.extract_url(url)[:3] == (share_id, "Q2w9", target or "/")
        query = parse_qs(urlsplit(url).query, keep_blank_values=True)
        assert query["source"] == ["app"]
        assert query["flag"] == [""]


def test_baidu_auto_update_keeps_passcode_when_selecting_child(monkeypatch):
    adapter, task, result, reads = _probe_listing(
        monkeypatch, object.__new__(BaiduAdapter), "https://pan.baidu.com/s/1Old",
        "https://pan.baidu.com/s/1Test?source=app#Q2w9",
        {"/": [_listed_dir("123", "4K")], "123": [_listed_file("e1", "Show.S01E01.mkv")]},
        {},
    )
    assert reads == ["/", "123"]
    assert result["updated"]
    assert adapter.extract_url(task.shareurl)[:3] == ("1Test", "Q2w9", "123")
    assert parse_qs(urlsplit(task.shareurl).query)["source"] == ["app"]


@pytest.mark.parametrize("url", [
    "https://yun.139.com/shareweb/?linkID=share139&passwd=Q2w9&fid=old",
    "https://caiyun.139.com/shareweb/#/w/i/share139?passwd=Q2w9&fid=old",
    "https://yun.139.com/shareweb/?share139",
    "https://yun.139.com/shareweb/?linkID=share139&passwd=Q2w9#/list/share/old",
    "https://yun.139.com/shareweb/?fid=legacy#/w/i/share139?passwd=Q2w9&fid=old",
])
def test_cloud139_rewrite_preserves_share_id_and_access_params(url):
    adapter = object.__new__(Cloud139Adapter)
    share, pwd, _, _ = adapter.extract_url(url)
    assert share == "share139"
    for target in ("new_folder", None):
        url = drama_share_autoupdate._rewrite_shareurl_with_fid(url, target)
        assert adapter.extract_url(url)[:3] == (share, pwd, target or "root")


if __name__ == "__main__":
    unittest.main()
