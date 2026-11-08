from __future__ import annotations

import unittest

from app.application.episode_number import episode_from_folder_name, extract_episode, guess_episode, parse_size


class _IdentityRename:
    _resolved_pattern = ""
    _resolved_replace = ""

    def sub(self, pattern: str, replace: str, value: str) -> str:  # pragma: no cover - identity
        return value


class _SeasonRename:
    _resolved_pattern = r"^(\d+)\.mp4$"
    _resolved_replace = r"S02E\1.mp4"

    def sub(self, pattern: str, replace: str, value: str) -> str:
        import re

        return re.sub(pattern, replace, value)


class EpisodeNumberTests(unittest.TestCase):
    def test_extract_episode_with_rename_rule(self) -> None:
        self.assertEqual(extract_episode("08.mp4", mr=_SeasonRename()), (2, 8))

    def test_extract_episode_without_rule_prefers_standard_names(self) -> None:
        self.assertEqual(extract_episode("Show.S01E07.mkv"), (1, 7))
        self.assertEqual(extract_episode("第12集.mp4"), (None, 12))

    def test_extract_episode_plain_name_needs_rule(self) -> None:
        self.assertEqual(extract_episode("08.mp4"), (None, None))

    def test_guess_episode_for_unrenamed_names(self) -> None:
        self.assertEqual(guess_episode("Show.S02E11.mkv"), 11)
        self.assertEqual(guess_episode("第3集 花絮.mp4"), 3)
        self.assertEqual(guess_episode("EP09.mp4"), 9)
        self.assertEqual(guess_episode("13.mp4"), 13)
        self.assertIsNone(guess_episode("1080p 合集.mp4"))

    def test_guess_episode_ignores_dates(self) -> None:
        self.assertIsNone(guess_episode("2026.1.13.mp4"))
        self.assertIsNone(guess_episode("2026-01-13.mp4"))
        self.assertIsNone(guess_episode("2026年1月13日.mp4"))
        self.assertIsNone(guess_episode("20260113.mp4"))
        self.assertIsNone(guess_episode("第20260113期.mp4"))
        self.assertEqual(guess_episode("2026.1.13 你好星期六 第5期.mp4"), 5)

    def test_guess_episode_skips_year_like_numbers(self) -> None:
        self.assertIsNone(guess_episode("2026.mp4"))
        self.assertIsNone(guess_episode("1999 合集.mp4"))
        self.assertEqual(guess_episode("26.zip"), 26)

    def test_episode_from_folder_name(self) -> None:
        self.assertEqual(episode_from_folder_name("4K SDR 60帧率 低码 26集"), 26)
        self.assertEqual(episode_from_folder_name("更新至30集"), 30)
        self.assertEqual(episode_from_folder_name("全12集"), 12)
        self.assertIsNone(episode_from_folder_name("4K DV 60帧率 高码"))
        self.assertIsNone(episode_from_folder_name("2026集"))

    def test_parse_size(self) -> None:
        self.assertEqual(parse_size("100MB"), 100 * 1024**2)
        self.assertEqual(parse_size("1.5gb"), int(1.5 * 1024**3))
        self.assertEqual(parse_size(""), 0)
        self.assertEqual(parse_size("abc"), 0)


if __name__ == "__main__":
    unittest.main()
