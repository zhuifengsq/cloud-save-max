from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.resource_search_source import ResourceSearchSource
from app.application.api_search_sources import (
    create_api_source,
    delete_api_source,
    list_api_sources,
    search_api_source,
    update_api_source,
)


class ApiSearchSourcesTest(unittest.TestCase):
    def test_create_update_delete_source(self) -> None:
        engine = create_engine("sqlite://")
        ResourceSearchSource.__table__.create(engine)
        with Session(engine) as db:
            created = create_api_source(db, {
                "name": "示例接口",
                "url": "https://example.test/search",
                "query_params": {"q": "{keyword}"},
            })
            self.assertEqual([item["name"] for item in list_api_sources(db)], ["示例接口"])
            updated = update_api_source(db, created["id"], {"enabled": False, "name": "新名称"})
            self.assertFalse(updated["enabled"])
            self.assertEqual(updated["name"], "新名称")
            delete_api_source(db, created["id"])
            self.assertEqual(list_api_sources(db), [])

    def test_maps_nested_json_and_keyword_to_task_links(self) -> None:
        class Response:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return {"data": {"results": [{"name": "电视剧 A", "link": {"url": "https://pan.quark.cn/s/abc"}, "date": "2026-10-27"}]}}

        config = {
            "name": "示例接口",
            "method": "GET",
            "url": "https://example.test/search",
            "query_params": {"keyword": "{keyword}"},
            "items_path": "data.results",
            "title_path": "name",
            "url_path": "link.url",
            "datetime_path": "date",
        }
        with patch("app.application.api_search_sources.requests.request", return_value=Response()) as request:
            results = search_api_source(config, "电视剧")
        self.assertEqual(request.call_args.kwargs["params"], {"keyword": "电视剧"})
        self.assertEqual(results[0]["shareurl"], "https://pan.quark.cn/s/abc")
        self.assertEqual(results[0]["taskname"], "电视剧 A")
        self.assertEqual(results[0]["source"], "示例接口")

    def test_pan_relay_style_keyword_and_jmespath_pairs(self) -> None:
        class Response:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return {"data": [{"title": "测试剧集", "link": "https://pan.quark.cn/s/abc"}]}

        config = {
            "name": "盘搜",
            "method": "POST",
            "url": "https://example.test/search?kw=[[keyword]]",
            "headers": {"X-Keyword": "[[keyword]]"},
            "body": {"text": "[[keyword]]"},
            "response_rule": "data[*].[title, link]",
        }
        with patch("app.application.api_search_sources.requests.request", return_value=Response()) as request:
            results = search_api_source(config, "测试剧集")
        self.assertIn("kw=%E6%B5%8B%E8%AF%95%E5%89%A7%E9%9B%86", request.call_args.args[1])
        self.assertEqual(request.call_args.kwargs["json"], {"text": "测试剧集"})
        self.assertEqual(request.call_args.kwargs["headers"], {"X-Keyword": "测试剧集"})
        self.assertEqual(results[0]["taskname"], "测试剧集")
        self.assertEqual(results[0]["shareurl"], "https://pan.quark.cn/s/abc")

    def test_jmespath_object_projection_keeps_password(self) -> None:
        class Response:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict:
                return {"rows": [{"name": "剧集 A", "share": "https://pan.baidu.com/s/abc", "pwd": "1234"}]}

        config = {
            "name": "测试源",
            "method": "GET",
            "url": "https://example.test/search?q=[[keyword]]",
            "response_rule": "rows[*].{title:name,url:share,password:pwd}",
        }
        with patch("app.application.api_search_sources.requests.request", return_value=Response()):
            results = search_api_source(config, "剧集")
        self.assertEqual(results[0]["password"], "1234")
        self.assertEqual(results[0]["taskname"], "剧集 A")


if __name__ == "__main__":
    unittest.main()
