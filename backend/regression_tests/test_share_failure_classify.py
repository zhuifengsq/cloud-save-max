"""分享失败分类：只有网盘能确定「失效」才提醒，其余一律按检测失败处理。"""

import pytest

from app.infrastructure.drive.adapters.baidu_adapter import BaiduAdapter
from app.infrastructure.drive.adapters.base_adapter import BaseCloudDriveAdapter
from app.infrastructure.drive.adapters.quark_adapter import QuarkAdapter
from app.application import generated_share_check


def _quark() -> QuarkAdapter:
    # 只测分类逻辑，不初始化网络会话
    return object.__new__(QuarkAdapter)


def _baidu() -> BaiduAdapter:
    return object.__new__(BaiduAdapter)


@pytest.mark.parametrize(
    "response, expected_kind",
    [
        ({"code": 41012}, "invalid"),
        ({"code": "41004"}, "invalid"),
        ({"status": 400, "code": 0, "message": "该分享已失效"}, "invalid"),
        ({"status": 400, "code": 41006, "message": "需要提取码"}, "passcode"),
        ({"status": 400, "code": 41008, "message": "提取码错误"}, "passcode"),
        ({"status": 500, "code": 31001, "message": "登录已失效"}, "auth"),
        ({"status": 400, "code": 999, "message": "访问频率过高"}, "throttled"),
        ({"status": 500, "code": 999, "message": "服务繁忙"}, "unknown"),
    ],
)
def test_quark_classification(response, expected_kind):
    assert _quark().classify_share_failure(response)["kind"] == expected_kind


@pytest.mark.parametrize(
    "response, expected_kind",
    [
        ({"code": -1, "message": "分享链接不存在"}, "invalid"),
        ({"code": -9, "message": "文件不存在或已被删除"}, "invalid"),
        ({"code": 105}, "invalid"),
        ({"code": 145}, "invalid"),
        ({"status": 400, "code": 1, "message": "分享链接无效或已失效"}, "invalid"),
        ({"code": -7, "message": "分享文件夹等待审核中"}, "pending"),
        ({"code": -62, "message": "需要输入验证码"}, "captcha"),
        ({"code": -6, "message": "认证失败，请检查Cookie是否有效"}, "auth"),
        ({"status": 500, "code": 1, "message": "百度网盘客户端未初始化"}, "auth"),
        ({"code": 4, "message": "分享提取码错误"}, "passcode"),
        ({"code": 200025}, "passcode"),
        ({"code": -65, "message": "访问频率过高"}, "throttled"),
        ({"code": 999, "message": "未知错误"}, "unknown"),
    ],
)
def test_baidu_classification(response, expected_kind):
    assert _baidu().classify_share_failure(response)["kind"] == expected_kind


def test_unimplemented_drive_is_unknown():
    # 未重写 classify_share_failure 的网盘一律 unknown，检测侧会当作「检测失败」而不是失效
    verdict = BaseCloudDriveAdapter.classify_share_failure(None, {"message": "奇怪错误"})
    assert verdict == {"kind": "unknown", "message": "奇怪错误"}


class _StubAdapter:
    def __init__(self, kind):
        self.kind = kind

    def classify_share_failure(self, response):
        return {"kind": self.kind, "message": "网盘原因"}


@pytest.mark.parametrize(
    "kind, expected_status, expected_message",
    [
        ("invalid", "invalid", "网盘原因"),
        ("passcode", "error", "分享提取码无法通过验证"),
        ("throttled", "error", "网盘请求频率过高，稍后重试"),
        ("auth", "error", "网盘原因"),
        ("unknown", "error", "检测失败兜底"),
        ("pending", "error", "检测失败兜底"),
    ],
)
def test_check_marks_only_invalid_as_failure(kind, expected_status, expected_message):
    status, message = generated_share_check._failure(
        _StubAdapter(kind), {}, "检测失败兜底",
    )
    assert (status, message) == (expected_status, expected_message)
