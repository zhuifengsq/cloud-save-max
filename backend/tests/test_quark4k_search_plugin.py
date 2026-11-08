from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.infrastructure.search.quark4k_plugin import Quark4KPlugin
from app.models.resource_search_source import ResourceSearchSource
from app.application.search_plugins import discover_search_plugins, list_search_plugins, normalize_search_results, search_enabled_plugins, set_search_plugin_enabled


class Quark4KPluginTests(unittest.TestCase):
    def test_discussion_pages_return_supported_share_links(self) -> None:
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                pass

            def json(self):
                return self.payload

        first_page = {
            "data": [
                {"attributes": {"title": "三体 4K", "createdAt": "2026-10-27T10:00:00Z"},
                 "relationships": {"mostRelevantPost": {"data": {"id": "42"}}}},
                {"attributes": {"title": "别的电视剧"},
                 "relationships": {"mostRelevantPost": {"data": {"id": "43"}}}},
            ],
            "included": [
                {"type": "posts", "id": "42", "attributes": {"contentHtml": '<p>资源 <a href="https://pan.quark.cn/s/abc">夸克</a> https://pan.baidu.com/s/def?pwd=1234 <a href="https://quark4k.com/d/1">论坛</a></p>'}},
                {"type": "posts", "id": "43", "attributes": {"contentHtml": '<a href="https://pan.quark.cn/s/skip">无关</a>'}},
            ],
        }
        second_page = {
            "data": [{"attributes": {"title": "三体 完结"}, "relationships": {"mostRelevantPost": {"data": {"id": "44"}}}}],
            "included": [{"type": "posts", "id": "44", "attributes": {"contentHtml": '<a href="https://pan.quark.cn/s/abc">重复</a>'}}],
        }

        def fake_get(url, *, params, **kwargs):
            self.assertEqual(url, "https://quark4k.com/api/discussions")
            self.assertEqual(params["filter[q]"], "三体")
            self.assertEqual(kwargs["timeout"], (3, 6))
            return Response(first_page if params["page[offset]"] == 0 else second_page)

        with patch("app.infrastructure.search.quark4k_plugin.requests.get", side_effect=fake_get) as mocked:
            raw = Quark4KPlugin().search(" 三体 ")

        self.assertEqual(mocked.call_count, 2)
        rows = normalize_search_results(Quark4KPlugin(), raw)
        self.assertEqual({row["shareurl"] for row in rows}, {
            "https://pan.quark.cn/s/abc",
            "https://pan.baidu.com/s/def?pwd=1234",
        })
        self.assertTrue(all("三体" in row["taskname"] for row in rows))
        self.assertEqual(next(row for row in rows if "quark" in row["shareurl"])["datetime"], "2026-10-27T10:00:00Z")

    def test_plugin_is_discoverable_and_starts_disabled(self) -> None:
        item = next(item for item in discover_search_plugins() if item["key"] == "quark4k")
        self.assertEqual(item["status"], "ready")
        self.assertEqual(item["name"], "Quark4K")
        engine = create_engine("sqlite://")
        ResourceSearchSource.__table__.create(engine)
        try:
            with Session(engine) as db:
                listed = next(item for item in list_search_plugins(db) if item["key"] == "quark4k")
                self.assertFalse(listed["enabled"])
                set_search_plugin_enabled(db, "quark4k", True)
                db.commit()

                class Response:
                    def raise_for_status(self):
                        pass

                    def json(self):
                        return {
                            "data": [{"attributes": {"title": "三体 4K"}, "relationships": {"mostRelevantPost": {"data": {"id": "1"}}}}],
                            "included": [{"type": "posts", "id": "1", "attributes": {"contentHtml": '<a href="https://pan.quark.cn/s/abc">资源</a>'}}],
                        }

                with patch("app.infrastructure.search.quark4k_plugin.requests.get", return_value=Response()):
                    results = search_enabled_plugins(db, "三体")
                self.assertEqual(results[0]["shareurl"], "https://pan.quark.cn/s/abc")
                self.assertEqual(results[0]["source"], "Quark4K")
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
