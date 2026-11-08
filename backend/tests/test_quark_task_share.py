from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from app.infrastructure.drive.adapters.quark_adapter import QuarkAdapter


class QuarkTaskShareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = QuarkAdapter(cookie="test-cookie")
        self.adapter.get_fids = Mock(return_value=[{"file_path": "/剧集/测试", "fid": "folder-1", "dir": True}])
        self.adapter.ls_dir = Mock(return_value={"code": 0, "data": {"list": [{"fid": "episode-1"}]}})

    def test_creates_public_link_for_exact_saved_folder(self) -> None:
        responses = [
            {"code": 0, "data": {"task_id": "share-task"}},
            {"code": 0, "data": {"share_id": "share-1"}},
            {"code": 0, "data": {"share_url": "https://pan.quark.cn/s/new-link"}},
        ]
        self.adapter._send_request = Mock(side_effect=[Mock(json=Mock(return_value=item)) for item in responses])

        result = self.adapter.create_share_link("/剧集/测试", "测试")

        self.assertEqual(result["share_url"], "https://pan.quark.cn/s/new-link")
        self.adapter.get_fids.assert_called_once_with(["/剧集/测试"])
        self.assertEqual(self.adapter._send_request.call_args_list[0].kwargs["json"]["fid_list"], ["folder-1"])

    def test_does_not_share_a_similarly_named_folder(self) -> None:
        self.adapter.get_fids.return_value = [{"file_path": "/剧集/测试旧", "fid": "wrong-folder", "dir": True}]
        self.adapter._send_request = Mock()

        with self.assertRaises(ValueError):
            self.adapter.create_share_link("/剧集/测试", "测试")

        self.adapter._send_request.assert_not_called()

    def test_does_not_share_an_empty_folder(self) -> None:
        self.adapter.ls_dir.return_value = {"code": 0, "data": {"list": []}}
        self.adapter._send_request = Mock()

        with self.assertRaises(ValueError):
            self.adapter.create_share_link("/剧集/测试", "测试")

        self.adapter._send_request.assert_not_called()

    def test_mobile_credentials_keep_share_creation_on_pc_api(self) -> None:
        adapter = QuarkAdapter(cookie="__puus=pc-token; kps=mobile; sign=signed; vcode=code")
        adapter._throttle_request = Mock()
        with patch("requests.request", return_value=Mock()) as request:
            adapter._send_request(
                "POST", "https://drive-pc.quark.cn/1/clouddrive/share",
                params={"pr": "ucpro", "fr": "pc"}, json={"fid_list": ["folder-1"]},
            )
        self.assertEqual(request.call_args.args[1], "https://drive-pc.quark.cn/1/clouddrive/share")
        self.assertIn("__puus=pc-token", request.call_args.kwargs["headers"]["cookie"])

    def test_mobile_credentials_still_use_mobile_sharepage_api(self) -> None:
        adapter = QuarkAdapter(cookie="__puus=pc-token; kps=mobile; sign=signed; vcode=code")
        adapter._throttle_request = Mock()
        with patch("requests.request", return_value=Mock()) as request:
            adapter._send_request(
                "POST", "https://drive-pc.quark.cn/1/clouddrive/share/sharepage/token",
                params={"pr": "ucpro", "fr": "pc"}, json={"pwd_id": "abc"},
            )
        self.assertEqual(request.call_args.args[1], "https://drive-m.quark.cn/1/clouddrive/share/sharepage/token")
        self.assertNotIn("cookie", request.call_args.kwargs["headers"])

    def test_non_json_share_response_has_actionable_error(self) -> None:
        response = Mock(status_code=200)
        response.json.side_effect = ValueError("Expecting value: line 1 column 1 (char 0)")
        self.adapter._send_request = Mock(return_value=response)

        with self.assertRaisesRegex(RuntimeError, "夸克分享接口返回非 JSON"):
            self.adapter.create_share_link("/剧集/测试", "测试")


if __name__ == "__main__":
    unittest.main()
