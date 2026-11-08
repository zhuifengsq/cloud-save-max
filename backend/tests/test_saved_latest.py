from __future__ import annotations

import unittest

from app.extensions.runtime.drama_executor import DramaTaskExecutor


def _executor(extra: dict | None = None, name: str = "测试任务") -> DramaTaskExecutor:
    return DramaTaskExecutor(adapter=object(), task_data={"taskname": name, "extra": extra or {}})


class SavedLatestTests(unittest.TestCase):
    def test_pool_includes_existing_destination_names(self) -> None:
        ex = _executor({"saved_latest": {"episode": 23, "name": "23.zip"}})
        ex._add_saved_names(["24.zip", "S01E25.mkv"])
        ex._record_saved_latest()
        self.assertIsNotNone(ex.saved_latest)
        assert ex.saved_latest is not None
        self.assertEqual(ex.saved_latest["episode"], 25)
        self.assertEqual(ex.saved_latest["season"], 1)

    def test_folder_hint_used_when_no_file_list(self) -> None:
        ex = _executor()
        ex._folder_episode_hints.append(26)
        ex._record_saved_latest()
        assert ex.saved_latest is not None
        self.assertEqual(ex.saved_latest["episode"], 26)
        self.assertEqual(ex.saved_latest["name"], "26集")

    def test_date_names_do_not_raise_episode(self) -> None:
        ex = _executor({"saved_latest": {"episode": 5}})
        ex._saved_name_pool.extend(["2026.1.13.mp4"])
        ex._record_saved_latest()
        assert ex.saved_latest is not None
        self.assertEqual(ex.saved_latest["episode"], 5)

    def test_empty_pool_keeps_previous(self) -> None:
        ex = _executor({"saved_latest": {"episode": 9, "name": "09.zip"}})
        ex._record_saved_latest()
        self.assertIsNone(ex.saved_latest)

    def test_year_like_previous_watermark_is_cleared(self) -> None:
        ex = _executor({"saved_latest": {"episode": 2026, "name": "2026.1.13.mp4"}})
        ex._saved_name_pool.extend(["2026.1.13.mp4", "2026.1.20.mp4"])
        ex._record_saved_latest()
        assert ex.saved_latest is not None
        self.assertEqual(ex.saved_latest["episode"], 0)

    def test_year_like_previous_does_not_block_new_episode(self) -> None:
        ex = _executor({"saved_latest": {"episode": 2026}})
        ex._saved_name_pool.extend(["26.zip"])
        ex._record_saved_latest()
        assert ex.saved_latest is not None
        self.assertEqual(ex.saved_latest["episode"], 26)


if __name__ == "__main__":
    unittest.main()
