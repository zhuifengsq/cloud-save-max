import json
import unittest
from unittest.mock import Mock, call, patch

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
        self.adapter._api_list_shared_paths.assert_called_once_with("/show", 123, 456, page=1)

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

    def _mock_share_directory(self, *pages):
        self.adapter._api_access_shared = Mock()
        self.adapter._api_shared_paths = Mock(return_value=self.data)
        self.adapter._api_list_shared_paths = Mock(side_effect=pages)

    @staticmethod
    def _episode(index):
        name = f"episode-{index}.mkv"
        return {"fs_id": index, "server_filename": name, "path": f"/show/{name}", "isdir": 0}

    def test_directory_without_total_reads_the_page_after_a_full_page(self):
        first_page = [self._episode(index) for index in range(1, 101)]
        self._mock_share_directory(
            {"errno": 0, "list": first_page}, {"errno": 0, "list": [self._episode(101)]},
        )

        result = self.adapter.get_detail("1test", "1test:1111", "/show")

        self.assertEqual(result["code"], 0, result.get("message"))
        self.assertEqual([item["fid"] for item in result["data"]["list"]], [str(i) for i in range(1, 102)])
        self.assertEqual(result["metadata"]["_total"], 101)
        self.assertEqual(self.adapter._api_list_shared_paths.call_count, 2)

    def test_directory_missing_items_after_an_advertised_total_fails(self):
        self._mock_share_directory(
            {"errno": 0, "list": [self._episode(1)], "total": 2},
            {"errno": 0, "list": [], "total": 2},
        )

        result = self.adapter.get_detail("1test", "1test:1111", "/show")

        self.assertNotEqual(result["code"], 0)
        self.assertEqual(result["data"]["list"], [])
        self.assertEqual(self.adapter._api_list_shared_paths.call_count, 2)

    def test_directory_api_error_is_not_a_successful_empty_directory(self):
        self._mock_share_directory({"errno": -9})

        result = self.adapter.get_detail("1test", "1test:1111", "/show")

        self.assertNotEqual(result["code"], 0)
        self.assertEqual(result["data"]["list"], [])
        self.assertEqual(self.adapter._api_list_shared_paths.call_count, 1)

    def test_valid_empty_subdirectory_is_still_empty(self):
        self._mock_share_directory({"errno": 0, "list": [], "total": 0})

        result = self.adapter.get_detail("1test", "1test:1111", "/show")

        self.assertEqual(result["code"], 0)
        self.assertEqual(result["data"]["list"], [])
        self.assertEqual(result["metadata"]["_total"], 0)
        self.adapter._api_list_shared_paths.assert_called_once_with("/show", 123, 456, page=1)

    def test_directory_discards_partial_items_after_a_second_page_error(self):
        self._mock_share_directory(
            {"errno": 0, "list": [self._episode(1)], "has_more": 1},
            {"errno": -9},
        )

        result = self.adapter.get_detail("1test", "1test:1111", "/show")

        self.assertNotEqual(result["code"], 0)
        self.assertEqual(result["data"]["list"], [])
        self.assertEqual(self.adapter._api_list_shared_paths.call_count, 2)

    def test_numeric_directory_lookup_finds_subfolder_on_second_page(self):
        nested = {"fs_id": 200, "server_filename": "season", "path": "/show/season", "isdir": 1}
        episode = dict(self._episode(201), path="/show/season/episode-201.mkv")
        self._mock_share_directory(
            {"errno": 0, "list": [self._episode(1)], "has_more": 1},
            {"errno": 0, "list": [nested], "has_more": 0},
            {"errno": 0, "list": [episode]},
        )

        result = self.adapter.get_detail("1test", "1test:1111", "200", fetch_share_full_path=1)

        self.assertEqual(result["code"], 0, result.get("message"))
        self.assertEqual([item["fid"] for item in result["data"]["list"]], ["201"])
        self.assertEqual(result["data"]["full_path"], [
            {"fid": "6174099023536", "file_name": "show"},
            {"fid": "200", "file_name": "season"},
        ])
        self.assertEqual(self.adapter._api_list_shared_paths.call_args_list, [
            call("/show", 123, 456, page=1), call("/show", 123, 456, page=2), call("/show/season", 123, 456, page=1),
        ])

    def test_missing_numeric_directory_does_not_use_the_first_shared_folder(self):
        listing = {"errno": 0, "list": [self._episode(1)]}
        self._mock_share_directory(listing, listing)

        result = self.adapter.get_detail("1test", "1test:1111", "200")

        self.assertNotEqual(result["code"], 0)
        self.assertEqual(result["data"]["list"], [])
        self.adapter._api_list_shared_paths.assert_called_once_with("/show", 123, 456, page=1)

    def test_preview_route_propagates_detail_failure(self):
        from app.application.share_preview import preview_share
        from app.application.dto.task_browse import SharePreviewIn
        from app.core.errors import ApiError
        adapter = Mock()
        adapter.extract_url.return_value = ("1test", "1111", "0", [])
        adapter.get_stoken.return_value = {"data": {"stoken": "token"}}
        adapter.get_detail.return_value = {"code": 1, "message": "读取分享目录失败", "data": {"list": []}}
        from app.infrastructure.drive.share_preview_reader import SharePreviewReader
        reader = SharePreviewReader(drive_type='baidu', account_name='test', cookie='fake', config={})
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        with patch('app.application.share_preview.share_preview_reader', return_value=reader), \
             patch('app.application.share_preview.get_enabled_magic_regex_map', return_value={}), \
             patch('app.infrastructure.drive.share_preview_reader.AdapterFactory.create_adapter', return_value=adapter):
            with Session(create_engine('sqlite://')) as db, self.assertRaises(ApiError) as caught:
                preview_share(SharePreviewIn(shareurl="https://pan.baidu.com/s/1test"), db)
        self.assertEqual(caught.exception.code, "TASK_SHARE_DETAIL_FAILED")
