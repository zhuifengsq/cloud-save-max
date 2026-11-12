"""审核中（pending）不能被当成「检测失败」，更不能发失败通知。

背景：百度转存接口对「审核中」的分享返回 errno=-7（分享文件夹等待审核中），
审核通过后链接就恢复可用。历史行为把它当普通失败：任务列表显示「检测失败」、
首页进失败列表、执行失败还推 ❌ 通知——全是误报。
"""
from __future__ import annotations

from types import SimpleNamespace

from app.application.generated_share_check import _map_kind
from app.application.notifications.task_notify import build_share_check_section, build_task_section
from app.infrastructure.execution.drama_executor import DramaTaskExecutor


class _PendingAdapter:
    DRIVE_TYPE = "baidu"

    def classify_share_failure(self, response):
        code = str((response or {}).get("code"))
        if code == "-7":
            return {"kind": "pending", "message": "分享文件夹等待审核中"}
        return {"kind": "unknown", "message": ""}


class _BrokenAdapter:
    DRIVE_TYPE = "baidu"

    def classify_share_failure(self, response):
        raise RuntimeError("adapter boom")


def test_map_kind_pending_yields_pending_status():
    status, message = _map_kind("pending", "分享文件夹等待审核中", "网盘暂时无法读取分享目录")
    assert status == "pending"
    assert "审核" in message


def test_map_kind_pending_falls_back_to_default_text():
    status, message = _map_kind("pending", "", "网盘暂时无法读取分享目录")
    assert status == "pending"
    assert message != "网盘暂时无法读取分享目录"
    assert "审核" in message


def test_pending_is_never_mapped_to_invalid():
    assert _map_kind("pending", "", "fallback")[0] != "invalid"


def test_pending_review_detected_by_adapter_classification():
    fake_self = SimpleNamespace(adapter=_PendingAdapter())
    assert DramaTaskExecutor._is_share_pending_review(
        fake_self, {"status": 200, "code": -7, "message": ""}
    ) is True
    assert DramaTaskExecutor._is_share_pending_review(
        fake_self, {"status": 200, "code": -1, "message": "分享链接不存在"}
    ) is False


def test_pending_review_falls_back_to_message_when_adapter_raises():
    fake_self = SimpleNamespace(adapter=_BrokenAdapter())
    assert DramaTaskExecutor._is_share_pending_review(
        fake_self, {"message": "分享文件夹等待审核中"}
    ) is True
    assert DramaTaskExecutor._is_share_pending_review(fake_self, {"message": "转存失败"}) is False


def test_pending_share_check_does_not_notify():
    # 就算快照里 notify 字段被误置为 True，pending 也不该生成通知段落
    snapshot = '{"generated_share_check": {"status": "pending", "message": "分享正在审核中", "notify": true}}'
    execution = SimpleNamespace(adapter_snapshot=snapshot)
    assert build_share_check_section(SimpleNamespace(taskname="剧"), execution) == ""


def test_skipped_execution_is_silent():
    task = SimpleNamespace(taskname="剧", savepath="/自动转存/剧")
    execution = SimpleNamespace(
        status="skipped",
        message="分享正在审核中，网盘审核通过后会自动重试",
        adapter_snapshot=None,
        tree_summary=None,
    )
    section, should_notify = build_task_section(task, execution)
    assert section == ""
    assert should_notify is False
