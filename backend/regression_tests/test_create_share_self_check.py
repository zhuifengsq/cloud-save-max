from __future__ import annotations

import unittest
from unittest.mock import Mock

from app.infrastructure.drive.adapters.baidu_adapter import BaiduAdapter

VALID_PAGE = {"errno": 0, "file_list": [{"path": "/剧集/测试/第1集.mp4"}]}
DEAD_PAGE = {"errno": -7, "share_page_type": "error", "file_list": []}
EMPTY_PAGE = {"errno": 0, "file_list": []}


def _make_adapter() -> BaiduAdapter:
    adapter = BaiduAdapter(cookie="BDUSS=test; STOKEN=test")
    adapter._SHARE_VERIFY_ATTEMPTS = 2
    adapter._SHARE_VERIFY_DELAY_SECONDS = 0
    adapter._api_access_shared = Mock(return_value={"errno": 0})
    return adapter


class VerifyCreatedShareTests(unittest.TestCase):
    """`_verify_created_share` 三态语义：valid / invalid / unknown（unknown 必须放行）。"""

    def test_valid_when_page_lists_files(self) -> None:
        adapter = _make_adapter()
        adapter._api_shared_paths = Mock(return_value=VALID_PAGE)

        ok, state, message = adapter._verify_created_share("https://pan.baidu.com/s/abc", "6666")

        self.assertTrue(ok)
        self.assertEqual(state, "valid")
        self.assertEqual(message, "")

    def test_invalid_when_share_page_reports_gone(self) -> None:
        adapter = _make_adapter()
        adapter._api_shared_paths = Mock(return_value=DEAD_PAGE)

        ok, state, message = adapter._verify_created_share("https://pan.baidu.com/s/abc", "6666")

        self.assertFalse(ok)
        self.assertEqual(state, "invalid")
        self.assertIn("不存在", message)

    def test_retries_before_declaring_dead(self) -> None:
        adapter = _make_adapter()
        adapter._SHARE_VERIFY_ATTEMPTS = 3
        adapter._api_shared_paths = Mock(side_effect=[DEAD_PAGE, EMPTY_PAGE, VALID_PAGE])

        ok, state, _ = adapter._verify_created_share("https://pan.baidu.com/s/abc", "6666")

        self.assertTrue(ok)
        self.assertEqual(state, "valid")
        self.assertEqual(adapter._api_shared_paths.call_count, 3)

    def test_unknown_when_read_raises(self) -> None:
        adapter = _make_adapter()
        adapter._api_shared_paths = Mock(side_effect=RuntimeError("network boom"))

        ok, state, message = adapter._verify_created_share("https://pan.baidu.com/s/abc", "6666")

        self.assertTrue(ok, "读不到不能证明链接失效，必须放行")
        self.assertEqual(state, "unknown")
        self.assertIn("network boom", message)

    def test_unknown_when_page_is_empty_without_error(self) -> None:
        adapter = _make_adapter()
        adapter._api_shared_paths = Mock(return_value=EMPTY_PAGE)

        ok, state, message = adapter._verify_created_share("https://pan.baidu.com/s/abc", "6666")

        self.assertTrue(ok)
        self.assertEqual(state, "unknown")
        self.assertTrue(message)


class CreateShareSelfCheckTests(unittest.TestCase):
    """生成分享后立即自检：确认是死链就报错，不再把链接返回给用户。"""

    def setUp(self) -> None:
        self.adapter = _make_adapter()
        self.adapter._api_list = Mock(return_value={"errno": 0, "list": [
            {"path": "/剧集/测试", "fs_id": 12345, "isdir": 1},
        ]})
        self.adapter.ls_dir = Mock(return_value={"code": 0, "data": {"list": [{"fid": "episode"}]}})
        self.adapter._get_bdstoken = Mock(return_value="token")
        self.adapter._request = Mock(return_value=Mock(json=Mock(return_value={
            "errno": 0, "link": "https://pan.baidu.com/s/abc", "shareid": 123,
        })))

    def test_returns_link_when_self_check_passes(self) -> None:
        self.adapter._api_shared_paths = Mock(return_value=VALID_PAGE)

        result = self.adapter.create_share_link("/剧集/测试", "测试")

        self.assertEqual(result["share_url"], "https://pan.baidu.com/s/abc?pwd=6666")
        # 自检必须走「不带提取码参数」的分享页地址，和真实访问一致
        self.adapter._api_shared_paths.assert_called_with("https://pan.baidu.com/s/abc")

    def test_raises_when_self_check_reports_dead_link(self) -> None:
        self.adapter._api_shared_paths = Mock(return_value=DEAD_PAGE)

        with self.assertRaises(RuntimeError) as ctx:
            self.adapter.create_share_link("/剧集/测试", "测试")

        self.assertIn("自检未通过", str(ctx.exception))
        self.assertIn("不存在", str(ctx.exception))

    def test_keeps_link_when_self_check_cannot_read(self) -> None:
        self.adapter._api_shared_paths = Mock(side_effect=RuntimeError("network boom"))

        result = self.adapter.create_share_link("/剧集/测试", "测试")

        self.assertEqual(result["share_url"], "https://pan.baidu.com/s/abc?pwd=6666")

    def test_does_not_self_check_when_share_request_fails(self) -> None:
        self.adapter._request = Mock(return_value=Mock(json=Mock(return_value={
            "errno": -6, "errmsg": "share failed",
        })))
        self.adapter._api_shared_paths = Mock(return_value=VALID_PAGE)

        with self.assertRaises(RuntimeError):
            self.adapter.create_share_link("/剧集/测试", "测试")

        self.adapter._api_shared_paths.assert_not_called()


if __name__ == "__main__":
    unittest.main()
