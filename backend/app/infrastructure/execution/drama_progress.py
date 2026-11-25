from __future__ import annotations

import re

from app.infrastructure.execution.magic_rename import MagicRename

_RE_SUPPLEMENTAL_FILE = re.compile(r"^彩蛋(?=$|[\d\s._（(\-]|第|[Ee][Pp]?\d)|角色番综")
_RE_DATED_TITLE = re.compile(r"^(?:19|20)\d{2}[.\-]\d{1,2}[.\-]\d{1,2}[-_\s]+\S")


class UnrecognizedEpisodeFiles(ValueError):
    """Bounded sample of file positions that strict progress correction rejected."""

    SAMPLE_LIMIT = 5

    def __init__(self, file_indices: list[int], total: int):
        super().__init__('Saved files have no recognizable episode number')
        self.file_indices = tuple(file_indices)
        self.total = total


def calculate_saved_latest(task_data: dict, file_names: list[str], folder_hints: list[int], *, at: str, preserve_previous: bool = True, require_all_recognized: bool = False, renamed_from: dict[str, str] | None = None) -> dict | None:
    """记录保存后目标目录里实际存在的最大集数（写到任务的 extra.saved_latest）。

    名字来源：目标目录已存在的文件 ∪ 本次转存成功的文件；整包直转时补充目标目录列表。
    这些清单在同步过程中本来就会拉取，不产生额外请求。
    手动校正的严格模式跳过彩蛋、角色番综和无集号的日期标题；其他未知文件仍拒绝校正。
    """
    from app.application.episode_number import extract_episode, guess_episode

    extra = task_data.get("extra") if isinstance(task_data.get("extra"), dict) else {}
    prev = extra.get("saved_latest") if isinstance(extra.get("saved_latest"), dict) else {}
    if not preserve_previous:
        prev = {}
    try:
        prev_episode = int(prev.get("episode") or 0)
    except (TypeError, ValueError):
        prev_episode = 0
    # 历史数据里可能把日期年份记成了集号（如 2026.1.13 -> 2026），直接丢弃
    prev_year_like = 1900 <= prev_episode <= 2100
    if prev_year_like:
        prev_episode = 0

    names = list(file_names)
    hints = list(folder_hints)
    if not names and not hints:
        if prev_year_like:
            # 清掉错误的年份水位，前端会隐藏"已存"
            return {"episode": 0, "season": None, "name": "", "at": at}
        return

    mr = MagicRename(magic_regex=(task_data.get("magic_regex") if isinstance(task_data.get("magic_regex"), dict) else None))
    mr.set_taskname(str(task_data.get("taskname") or ""))
    pattern = str(task_data.get("pattern") or "")
    replace = str(task_data.get("replace") or "")
    if pattern and replace:
        try:
            pattern, replace = mr.magic_regex_conv(pattern, replace)
            mr._resolved_pattern = pattern
            mr._resolved_replace = replace
        except Exception:
            mr = None
    else:
        mr = None

    previous_name = str(prev.get("name") or "")
    try:
        previous_season = int(prev["season"]) if prev.get("season") is not None else None
    except (TypeError, ValueError, OverflowError):
        previous_season = None
    if previous_season is None and prev_episode > 0:
        name_season, name_episode = extract_episode(previous_name)
        if name_episode == prev_episode:
            previous_season = name_season

    known_seasons: set[int] = set()
    seasonal: tuple[int, int, str] | None = None
    unseasoned = (0, "")

    def consider(season: int | None, episode: int, name: str) -> None:
        nonlocal seasonal, unseasoned
        if episode <= 0:
            return
        renamed_origin = (renamed_from or {}).get(name)
        if season is None:
            if episode > unseasoned[0] or (episode == unseasoned[0] and renamed_origin == unseasoned[1]):
                unseasoned = (episode, name)
            return
        known_seasons.add(season)
        if seasonal is None or (season, episode) > seasonal[:2] or (
            (season, episode) == seasonal[:2] and renamed_origin == seasonal[2]
        ):
            seasonal = (season, episode, name)

    consider(previous_season, prev_episode, previous_name)
    unrecognized_indices = []
    unrecognized_count = 0
    for index, name in enumerate(names):
        if require_all_recognized and _RE_SUPPLEMENTAL_FILE.search(name.strip()):
            continue
        season, episode = extract_episode(name)
        if season is None and mr is not None:
            resolved_season, resolved_episode = extract_episode(name, mr=mr)
            if resolved_episode is not None:
                season, episode = resolved_season, resolved_episode
        if episode is None:
            episode = guess_episode(name)
        if require_all_recognized and episode is None and _RE_DATED_TITLE.match(name.strip()):
            continue
        if episode is None or (require_all_recognized and episode <= 0):
            if require_all_recognized:
                unrecognized_count += 1
                if len(unrecognized_indices) < UnrecognizedEpisodeFiles.SAMPLE_LIMIT:
                    unrecognized_indices.append(index)
            continue
        consider(season, int(episode), name)
    if unrecognized_count:
        raise UnrecognizedEpisodeFiles(unrecognized_indices, unrecognized_count)
    for hint in hints:
        try:
            hint_value = int(hint)
        except (TypeError, ValueError):
            continue
        consider(None, hint_value, f"{hint_value}集")
    # 已明确跨季时，以季号为准。单季和无季号的旧数据仍可按集号推进。
    if seasonal is not None and (len(known_seasons) > 1 or seasonal[1] >= unseasoned[0]):
        best_season, best_episode, best_name = seasonal
    else:
        best_season = None
        best_episode, best_name = unseasoned
    if not best_episode:
        if prev_year_like:
            return {"episode": 0, "season": None, "name": "", "at": at}
        return
    return {
        "episode": int(best_episode),
        "season": best_season,
        "name": best_name,
        "at": at,
    }
