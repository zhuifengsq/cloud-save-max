from __future__ import annotations

from app.infrastructure.execution.magic_rename import MagicRename

def calculate_saved_latest(task_data: dict, file_names: list[str], folder_hints: list[int], *, at: str, preserve_previous: bool = True, require_all_recognized: bool = False) -> dict | None:
    """记录保存后目标目录里实际存在的最大集数（写到任务的 extra.saved_latest）。

    名字来源：目标目录已存在的文件 ∪ 本次转存成功的文件；整包直转时补充目标目录列表。
    这些清单在同步过程中本来就会拉取，不产生额外请求。
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

    best_episode = prev_episode
    best_season = prev.get("season")
    best_name = str(prev.get("name") or "")
    for name in names:
        season, episode = extract_episode(name, mr=mr)
        if episode is None:
            episode = guess_episode(name)
        if episode is None or (require_all_recognized and episode <= 0):
            if require_all_recognized:
                raise ValueError('A saved file has no recognizable episode number')
            continue
        if int(episode) > int(best_episode or 0):
            best_episode = int(episode)
            best_season = season
            best_name = name
    for hint in hints:
        try:
            hint_value = int(hint)
        except (TypeError, ValueError):
            continue
        if hint_value > int(best_episode or 0):
            best_episode = hint_value
            best_season = None
            best_name = f"{hint_value}集"
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
