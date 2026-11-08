from __future__ import annotations

import unittest

from app.application.drama_update_progress import build_drama_update_progress


def _details(*, last_season=1, last_episode=10, status="Returning Series"):
    return {
        "status": status,
        "last_episode_to_air": {"season_number": last_season, "episode_number": last_episode},
        "next_episode_to_air": None,
    }


class DramaUpdateProgressTests(unittest.TestCase):
    def test_without_tmdb_reports_saved_episode(self) -> None:
        out = build_drama_update_progress(
            tmdb_details=None,
            saved_latest={"episode": 6, "season": 1, "name": "Show.S01E06.mp4", "at": "2026-10-27T20:00:00+08:00"},
        )
        self.assertTrue(out.available)
        self.assertEqual(out.saved_episode, 6)
        self.assertIsNotNone(out.snapshot_captured_at)
        self.assertIn("2026-10-27", str(out.snapshot_captured_at))

    def test_behind_episodes_computed_from_tmdb_and_saved(self) -> None:
        out = build_drama_update_progress(
            tmdb_details=_details(last_episode=10),
            saved_latest={"episode": 7, "season": 1},
        )
        self.assertTrue(out.available)
        self.assertEqual(out.saved_episode, 7)
        self.assertEqual(out.tmdb_episode, 10)
        self.assertEqual(out.behind_episodes, 3)
        self.assertFalse(out.is_latest)

    def test_latest_episode_marks_is_latest(self) -> None:
        out = build_drama_update_progress(
            tmdb_details=_details(last_episode=10),
            saved_latest={"episode": 10, "season": 1},
        )
        self.assertTrue(out.available)
        self.assertTrue(out.is_latest)
        self.assertEqual(out.behind_episodes, 0)

    def test_missing_saved_latest_is_not_available(self) -> None:
        out = build_drama_update_progress(tmdb_details=_details(), saved_latest=None)
        self.assertFalse(out.available)
        self.assertEqual(out.tmdb_episode, 10)

    def test_season_mismatch_is_not_available(self) -> None:
        out = build_drama_update_progress(
            tmdb_details=_details(last_season=1),
            saved_latest={"episode": 3, "season": 2},
        )
        self.assertFalse(out.available)
        self.assertEqual(out.saved_episode, 3)


if __name__ == "__main__":
    unittest.main()
