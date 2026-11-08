from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.models.resource_search_source import ResourceSearchSource
from app.services.search_plugins import (
    discover_search_plugins,
    list_search_plugins,
    search_enabled_plugins,
    set_search_plugin_enabled,
    upload_search_plugin,
)


SOURCE = b'''from app.extensions.search_plugins.base import BaseSearchPlugin
class DemoPlugin(BaseSearchPlugin):
    key = "demo"
    name = "Demo"
    description = "Test plugin"
    def search(self, keyword):
        return [
            {"title": keyword, "url": "https://pan.quark.cn/s/abc"},
            {"title": "duplicate", "url": "https://pan.quark.cn/s/abc"},
            {"title": "invalid", "url": "file:///secret"},
        ]
'''


class SearchPluginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.plugin_dir = Path(self.temp.name)
        self.engine = create_engine("sqlite://")
        ResourceSearchSource.__table__.create(self.engine)
        self.db = Session(self.engine)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        self.temp.cleanup()

    def test_discovery_enable_and_result_normalization(self):
        (self.plugin_dir / "demo_plugin.py").write_bytes(SOURCE)
        plugins = list_search_plugins(self.db, plugin_dir=self.plugin_dir)
        self.assertEqual(plugins[0]["status"], "ready")
        self.assertFalse(plugins[0]["enabled"])
        self.assertEqual(search_enabled_plugins(self.db, "movie", plugin_dir=self.plugin_dir), [])

        set_search_plugin_enabled(self.db, "demo", True, plugin_dir=self.plugin_dir)
        self.db.commit()
        results = search_enabled_plugins(self.db, "movie", plugin_dir=self.plugin_dir)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["taskname"], "movie")
        self.assertEqual(results[0]["shareurl"], "https://pan.quark.cn/s/abc")
        self.assertEqual(results[0]["source"], "Demo")

    def test_upload_validates_before_creating_final_file(self):
        with self.assertRaises(ApiError):
            upload_search_plugin("../demo_plugin.py", SOURCE, plugin_dir=self.plugin_dir)
        with self.assertRaises(ApiError):
            upload_search_plugin("demo_plugin.py", b"def invalid(:", plugin_dir=self.plugin_dir)
        self.assertFalse((self.plugin_dir / "demo_plugin.py").exists())

        item = upload_search_plugin("demo_plugin.py", SOURCE, plugin_dir=self.plugin_dir)
        self.assertEqual(item["key"], "demo")
        self.assertEqual(discover_search_plugins(plugin_dir=self.plugin_dir)[0]["status"], "ready")
        with self.assertRaises(ApiError):
            upload_search_plugin("demo_plugin.py", b"# replacement", plugin_dir=self.plugin_dir)
        self.assertEqual((self.plugin_dir / "demo_plugin.py").read_bytes(), SOURCE)


if __name__ == "__main__":
    unittest.main()
