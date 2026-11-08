import json
import unittest
from unittest.mock import Mock, patch

from app.infrastructure.drive.adapters.baidu_adapter import BaiduAdapter


class BaiduSharePreviewTests(unittest.TestCase):
    def setUp(self):
        self.adapter = BaiduAdapter(cookie="BDUSS=test")
        self.data = {"share_uk": 123, "shareid": 456, "file_list": [
            {"fs_id": 6174099023536, "isdir": 1, "path": "/show", "server_filename": "show"}
        ]}

    def page(self, html):
        response = Mock(text=html)
        response.cookies.get_dict.return_value = {}
        self.adapter._request = Mock(return_value=response)

    def test_json_script_supports_subfolder_listing(self):
        self.page('<script type="application/json">' + json.dumps(self.data) + '</script>')
        self.adapter._api_access_shared = Mock()
        self.adapter._api_list_shared_paths = Mock(return_value={"errno": 0, "list": [
            {"fs_id": 99, "isdir": 0, "path": "/show/01.mp4", "server_filename": "01.mp4"}
        ]})
        result = self.adapter.get_detail("1test", "1test:1111", "6174099023536")
        self.assertEqual([x["file_name"] for x in result["data"]["list"]], ["01.mp4"])
        self.adapter._api_list_shared_paths.assert_called_once_with("/show", 123, 456)

    def test_legacy_multiline_json_and_delimiters_in_filename(self):
        self.data["file_list"][0]["server_filename"] = "show);special"
        self.page('<script>locals.mset(' + json.dumps(self.data, indent=2) + ');</script>')
        self.assertEqual(self.adapter._api_shared_paths("https://pan.baidu.com/s/test"), self.data)

    def test_ignores_unrelated_json_before_share_data(self):
        self.page('<script type="application/json">{"foo":1}</script><script type="application/json">'
                  + json.dumps(self.data) + '</script>')
        self.assertEqual(self.adapter._api_shared_paths("url"), self.data)

    def test_unreadable_page_is_not_successful_empty_directory(self):
        self.page('<html>请输入提取码</html>')
        self.adapter._api_access_shared = Mock()
        result = self.adapter.get_detail("1test", "1test:1111", "0")
        self.assertNotEqual(result["code"], 0)

    def test_valid_empty_share_is_still_empty(self):
        self.data["file_list"] = []
        self.page('<script type="application/json">' + json.dumps(self.data) + '</script>')
        self.adapter._api_access_shared = Mock()
        result = self.adapter.get_detail("1test", "1test:1111", "0")
        self.assertEqual(result["code"], 0)
        self.assertEqual(result["data"]["list"], [])

    def test_preview_route_propagates_detail_failure(self):
        from app.api.routes.tasks_share import post_share_preview
        from app.schemas.task_browse import SharePreviewIn
        from app.core.errors import ApiError
        adapter = Mock()
        adapter.extract_url.return_value = ("1test", "1111", "0", [])
        adapter.get_stoken.return_value = {"data": {"stoken": "token"}}
        adapter.get_detail.return_value = {"code": 1, "message": "读取分享目录失败", "data": {"list": []}}
        manager = Mock()
        manager.get_adapter_for_task.return_value = adapter
        with patch("app.api.routes.tasks_share.DatabaseAccountManager", return_value=manager), \
             patch("app.api.routes.tasks_share.pick_default_account_name", return_value="test"):
            with self.assertRaises(ApiError) as caught:
                post_share_preview(SharePreviewIn(shareurl="https://pan.baidu.com/s/1test"), Mock())
        self.assertEqual(caught.exception.code, "TASK_SHARE_DETAIL_FAILED")
