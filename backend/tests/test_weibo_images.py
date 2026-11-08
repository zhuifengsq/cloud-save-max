from __future__ import annotations

import pytest

pytest.skip(
    "weibo 功能已从代码库删除（app.services.weibo_images / app.models.weibo_worker 不存在），测试待重写；" 
    "见 docs/architecture-evolution-plan.md 的 S0 项",
    allow_module_level=True,
)

import os
import tempfile
import unittest

from app.services.weibo_images import WeiboImageError, _validate_source_url, image_file_path, resolve_weibo_root


class WeiboImageSecurityTest(unittest.TestCase):
    def test_only_allowlisted_https_hosts(self):
        self.assertEqual(
            _validate_source_url("https://image.tmdb.org/t/p/original/poster.jpg"),
            "https://image.tmdb.org/t/p/original/poster.jpg",
        )
        for url in (
            "http://image.tmdb.org/t/p/original/poster.jpg",
            "https://127.0.0.1/poster.jpg",
            "https://localhost/poster.jpg",
            "https://evil.example/poster.jpg",
            "https://image.tmdb.org.evil.example/poster.jpg",
            "https://image.tmdb.org:8443/poster.jpg",
            "https://user:pass@image.tmdb.org/poster.jpg",
        ):
            with self.subTest(url=url):
                with self.assertRaises(WeiboImageError):
                    _validate_source_url(url)

    def test_image_path_traversal_is_rejected(self):
        with self.assertRaises(WeiboImageError):
            image_file_path({"path": "../../backend/data/secrets.json"})
        with self.assertRaises(WeiboImageError):
            image_file_path({"path": ""})

    def test_root_resolves_next_to_sqlite_database(self):
        path = resolve_weibo_root(database_url="sqlite:///./data/app.db", explicit_dir=None)
        self.assertTrue(path.replace("\\", "/").endswith("data/weibo"))


if __name__ == "__main__":
    unittest.main()
