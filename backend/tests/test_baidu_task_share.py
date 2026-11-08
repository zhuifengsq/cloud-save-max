from __future__ import annotations

import unittest
from unittest.mock import Mock

from app.infrastructure.drive.adapters.baidu_adapter import BaiduAdapter


class BaiduTaskShareTests(unittest.TestCase):
    def setUp(self) -> None:
        self.adapter = BaiduAdapter(cookie="BDUSS=test; STOKEN=test")
        self.adapter._api_list = Mock(return_value={"errno": 0, "list": [
            {"path": "/剧集/测试", "fs_id": 12345, "isdir": 1},
        ]})
        self.adapter.ls_dir = Mock(return_value={"code": 0, "data": {"list": [{"fid": "episode"}]}})
        self.adapter._get_bdstoken = Mock(return_value="token")

    def test_creates_password_protected_link(self) -> None:
        self.adapter._request = Mock(return_value=Mock(json=Mock(return_value={
            "errno": 0, "link": "https://pan.baidu.com/s/abc", "shareid": 123,
        })))

        result = self.adapter.create_share_link("/剧集/测试", "测试")

        self.assertEqual(result["share_url"], "https://pan.baidu.com/s/abc?pwd=6666")
        self.assertEqual(result["passcode"], "6666")
        data = self.adapter._request.call_args.kwargs["data"]
        self.assertEqual(data["pwd"], "6666")
        self.assertEqual(data["fid_list"], "[\"12345\"]")

    def test_creates_permanent_share_by_default(self) -> None:
        self.adapter._request = Mock(return_value=Mock(json=Mock(return_value={
            "errno": 0, "link": "https://pan.baidu.com/s/abc", "shareid": 123,
        })))

        result = self.adapter.create_share_link("/剧集/测试", "测试")

        self.assertEqual(self.adapter._request.call_args.kwargs["data"]["period"], "0")
        self.assertNotIn("expires_in_days", result)
        self.adapter._request.assert_called_once()

    def test_falls_back_to_seven_days_when_permanent_rejected(self) -> None:
        responses = [
            Mock(json=Mock(return_value={"errno": -3, "errmsg": "period invalid"})),
            Mock(json=Mock(return_value={"errno": 0, "link": "https://pan.baidu.com/s/abc", "shareid": 123})),
        ]
        self.adapter._request = Mock(side_effect=responses)

        result = self.adapter.create_share_link("/剧集/测试", "测试")

        self.assertEqual(result["expires_in_days"], "7")
        periods = [call.kwargs["data"]["period"] for call in self.adapter._request.call_args_list]
        self.assertEqual(periods, ["0", "7"])

    def test_rejects_missing_folder_before_share_request(self) -> None:
        self.adapter._api_list.return_value = {"errno": 0, "list": [
            {"path": "/剧集/测试旧", "fs_id": 67890, "isdir": 1},
        ]}
        self.adapter._request = Mock()

        with self.assertRaises(ValueError):
            self.adapter.create_share_link("/剧集/测试", "测试")

        self.adapter._request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
