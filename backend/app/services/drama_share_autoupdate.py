from __future__ import annotations

import json
import logging
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.extensions.runtime.adapter_registry import AdapterRegistry
from app.models.drive_account import DriveAccount
from app.services.episode_number import extract_episode, guess_episode, parse_size
from app.services.resource_search import fetch_task_suggestions
from app.services.tmdb_cache import get_tmdb_detail_cached

logger = logging.getLogger(__name__)

# 换链只做“死链修复”，单个任务一次最多探测的候选数量
MAX_PROBE_CANDIDATES = 8


def _task_extra(task: object) -> dict:
    raw = getattr(task, "extra_json", None)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _task_addition(task: object) -> dict:
    raw = getattr(task, "addition_json", None)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _is_auto_update_enabled(task: object) -> bool:
    extra = _task_extra(task)
    return bool(extra.get("auto_update_shareurl") or extra.get("auto_update_115_shareurl"))


def _pick_drive_type(db: Session, task: object) -> str | None:
    account_name = str(getattr(task, "account_name", "") or "").strip()
    if account_name:
        row = db.execute(select(DriveAccount.drive_type).where(DriveAccount.name == account_name)).scalars().first()
        if row:
            dt = str(row or "").strip()
            return dt or None
    detected = AdapterRegistry.detect_drive_type(str(getattr(task, "shareurl", "") or ""))
    return str(detected or "").strip() or None


def is_auto_update_task(db: Session, task: object, *, respect_toggle: bool = True) -> bool:
    if str(getattr(task, "task_type", "") or "") != "drama":
        return False
    if not _pick_drive_type(db, task):
        return False
    if respect_toggle and not _is_auto_update_enabled(task):
        return False
    tmdb_id = int(getattr(task, "tmdb_id", 0) or 0)
    if tmdb_id > 0 and str(getattr(task, "tmdb_media_type", "") or "").strip().lower() != "tv":
        return False
    return True


# 兼容旧调用
is_115_auto_update_task = is_auto_update_task


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _rewrite_shareurl_with_fid(shareurl: str, fid: str | None) -> str:
    """把分享链接定位到某个子目录（fid）；fid 为空时去掉链接里的目录定位。"""
    url = str(shareurl or "").strip()
    f = str(fid or "").strip()
    if "yun.139.com" in url or "caiyun.139.com" in url:
        parsed = urlsplit(url)
        if parsed.fragment:
            frag_path, frag_query = (parsed.fragment.split("?", 1) + [""])[:2]
            frag_pairs = [(k, v) for k, v in parse_qsl(frag_query, keep_blank_values=True) if str(k).lower() != "fid"]
            if f and f not in ("0", "root"):
                frag_pairs.append(("fid", f))
            rebuilt_fragment = frag_path if not frag_pairs else f"{frag_path}?{urlencode(frag_pairs)}"
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, rebuilt_fragment)).strip()
        query_pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if str(k).lower() != "fid"]
        if f and f not in ("0", "root"):
            query_pairs.append(("fid", f))
        return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query_pairs), parsed.fragment)).strip()
    if not f or f == "0":
        return url.split("#")[0].strip()
    if f in url:
        return url
    return f"{url.split('#')[0].strip()}#/list/share/{f}"


def _search_candidates(
    db: Session,
    *,
    names: list[str],
    drive_type: str,
    search_filter: str = "",
    search_exclude: str = "",
    search_date_from: str = "",
    search_filter_mode: str = "",
    search_exclude_mode: str = "",
) -> tuple[list[dict], bool, dict]:
    all_items: list[dict] = []
    db_changed = False
    for keyword in names:
        items, changed, _msg = fetch_task_suggestions(
            db,
            keyword=keyword,
            deep=1,
            drive_type=drive_type,
            search_filter=search_filter,
            search_exclude=search_exclude,
            search_date_from=search_date_from,
            search_filter_mode=search_filter_mode,
            search_exclude_mode=search_exclude_mode,
        )
        if changed:
            db_changed = True
        if isinstance(items, list):
            all_items.extend([x for x in items if isinstance(x, dict)])
    stats: dict = {
        "keywords": [str(x or "").strip() for x in names if str(x or "").strip()],
        "fetched_total": len(all_items),
        "unique_shareurl_total": 0,
        "skip_wrong_drive": 0,
        "kept": 0,
    }
    filtered: list[dict] = []
    seen_urls: set[str] = set()
    for item in all_items:
        shareurl = str(item.get("shareurl") or "").strip()
        if not shareurl or shareurl in seen_urls:
            continue
        stats["unique_shareurl_total"] = int(stats.get("unique_shareurl_total") or 0) + 1
        if AdapterRegistry.detect_drive_type(shareurl) != drive_type:
            stats["skip_wrong_drive"] = int(stats.get("skip_wrong_drive") or 0) + 1
            continue
        seen_urls.add(shareurl)
        filtered.append(item)
        stats["kept"] = int(stats.get("kept") or 0) + 1
    return filtered, db_changed, stats


def _prepare_candidates(db: Session, *, suggestions: list[dict], addition: dict) -> tuple[list[dict], dict]:
    """过滤失效链接、优选/屏蔽分享者，并按“优选优先 + 发布时间新到旧”排序。"""
    from app.services.share_preview_batch import validate_share_links_streaming

    stats: dict = {"skip_invalid": 0, "skip_non_preferred": 0, "skip_blocked": 0}
    candidates = list(suggestions)
    urls = [str(item.get("shareurl") or "").strip() for item in candidates if str(item.get("shareurl") or "").strip()]
    if urls:
        valid_urls: set[str] = set()
        author_map: dict[str, str] = {}
        for row in validate_share_links_streaming(db, urls):
            if row.ok:
                valid_urls.add(row.shareurl)
                author = str(row.share_author_name or "").strip()
                if author:
                    author_map[row.shareurl] = author
        for item in candidates:
            url = str(item.get("shareurl") or "").strip()
            if url in author_map and not str(item.get("share_author_name") or "").strip():
                item["share_author_name"] = author_map[url]
        before = len(candidates)
        candidates = [item for item in candidates if str(item.get("shareurl") or "").strip() in valid_urls]
        stats["skip_invalid"] = before - len(candidates)

    from app.api.routes.system_settings import _get_setting

    preferred_str = str(_get_setting(db, "preferred_sharers") or "").strip()
    preferred_set = {s.strip() for s in preferred_str.split("|") if s.strip()} if preferred_str else set()

    if bool(addition.get("preferred_only")) and preferred_set:
        before = len(candidates)
        candidates = [item for item in candidates if str(item.get("share_author_name") or "").strip() in preferred_set]
        stats["skip_non_preferred"] = before - len(candidates)

    if not bool(addition.get("show_blocked")):
        from app.services.resource_search import filter_blocked_sharers

        before = len(candidates)
        candidates = filter_blocked_sharers(db, candidates)
        stats["skip_blocked"] = before - len(candidates)

    def _pub_ts(item: dict) -> float:
        from datetime import datetime

        try:
            return datetime.fromisoformat(str(item.get("datetime") or "").replace("Z", "+00:00")).timestamp()
        except Exception:
            return 0

    def _sort_key(item: dict):
        author = str(item.get("share_author_name") or "").strip()
        is_preferred = 1 if author in preferred_set else 0
        return (is_preferred, _pub_ts(item))

    candidates.sort(key=_sort_key, reverse=True)
    return candidates, stats


def _pick_fid_for_normal_mode(groups: list) -> str | None:
    """普通模式：挑一个最值得定位的子目录（文件最多，其次文件时间最新）。"""
    best_fid: str | None = None
    best_score: tuple[int, int] = (-1, -1)
    for group in groups:
        if not isinstance(group, (tuple, list)) or len(group) < 4:
            continue
        file_list, fid, _dir_ts, _dir_name = group[0], group[1], group[2], group[3]
        if not file_list or not fid:
            continue
        latest_ts = 0
        for item in file_list:
            try:
                ts = int(float(item.get("updated_at") or 0))
            except (TypeError, ValueError):
                ts = 0
            latest_ts = max(latest_ts, ts)
        score = (len(file_list), latest_ts)
        if score > best_score:
            best_score = score
            best_fid = str(fid)
    return best_fid


def resolve_drama_shareurl_update(
    db: Session,
    task: object,
    *,
    respect_toggle: bool = True,
    tried_shareurls: set[str] | None = None,
) -> dict:
    """失效链接修复：搜到一条可用的新链接就写回任务（不比较集数/内容）。"""
    if not is_auto_update_task(db, task, respect_toggle=respect_toggle):
        return {"checked": False, "updated": False, "reason": "not_applicable"}

    task_id = int(getattr(task, "id", 0) or 0)
    old_shareurl = str(getattr(task, "shareurl", "") or "").strip()
    if not old_shareurl:
        return {"checked": False, "updated": False, "reason": "shareurl_empty"}

    logger.info("自动换链（死链修复）：开始 task_id=%s", task_id)
    drive_type = _pick_drive_type(db, task) or "quark"
    addition = _task_addition(task)
    folder_mode = str(addition.get("folder_mode") or "normal").strip().lower()

    search_filter = str(addition.get("search_filter") or "").strip()
    search_exclude = str(addition.get("search_exclude") or "").strip()
    search_date_from = str(addition.get("search_date_from") or "").strip()
    search_filter_mode = str(addition.get("search_filter_mode") or "").strip()
    search_exclude_mode = str(addition.get("search_exclude_mode") or "").strip()
    folder_filter = str(addition.get("folder_filter") or "").strip()
    folder_exclude = str(addition.get("folder_exclude") or "").strip()
    folder_filter_mode = str(addition.get("folder_filter_mode") or "").strip()
    folder_exclude_mode = str(addition.get("folder_exclude_mode") or "").strip()
    folder_priority = str(addition.get("folder_priority") or "").strip()
    folder_priority_mode = str(addition.get("folder_priority_mode") or "").strip()

    names = _dedupe_preserve_order([str(getattr(task, "taskname", "") or "").strip()])
    tmdb_id = int(getattr(task, "tmdb_id", 0) or 0)
    if tmdb_id > 0:
        try:
            configured, detail, _u, _e, _r = get_tmdb_detail_cached(db, media_type="tv", tmdb_id=tmdb_id)
            if configured and isinstance(detail, dict):
                tmdb_names = _dedupe_preserve_order(
                    [str(detail.get("name") or "").strip(), str(detail.get("original_name") or "").strip()]
                )
                if tmdb_names:
                    names = tmdb_names
        except Exception:
            pass
    if not names:
        return {"checked": False, "updated": False, "reason": "no_search_keyword"}

    suggestions, db_changed, search_stats = _search_candidates(
        db,
        names=names,
        drive_type=drive_type,
        search_filter=search_filter,
        search_exclude=search_exclude,
        search_date_from=search_date_from,
        search_filter_mode=search_filter_mode,
        search_exclude_mode=search_exclude_mode,
    )
    if not suggestions:
        return {
            "checked": True,
            "updated": False,
            "reason": "no_candidates",
            "reason_detail": {"search": search_stats},
            "db_changed": bool(db_changed),
        }

    candidates, filter_stats = _prepare_candidates(db, suggestions=suggestions, addition=addition)
    if not candidates:
        return {
            "checked": True,
            "updated": False,
            "reason": "no_valid_candidate",
            "reason_detail": {"search": search_stats, "filter": filter_stats},
            "db_changed": bool(db_changed),
        }

    from app.services.share_preview_batch import fetch_share_file_list_grouped

    if tried_shareurls is None:
        tried_shareurls = set()
    preview_stats: dict = {"probed": 0, "skip_unpreviewable": 0}

    for index, suggestion in enumerate(candidates[:MAX_PROBE_CANDIDATES]):
        shareurl = str(suggestion.get("shareurl") or "").strip()
        if not shareurl or shareurl == old_shareurl:
            continue
        if shareurl in tried_shareurls:
            logger.info("  候选%d：已尝试过，跳过", index + 1)
            continue
        tried_shareurls.add(shareurl)
        preview_stats["probed"] = int(preview_stats["probed"]) + 1
        groups = fetch_share_file_list_grouped(
            db,
            shareurl,
            folder_filter=folder_filter,
            folder_exclude=folder_exclude,
            folder_filter_mode=folder_filter_mode,
            folder_exclude_mode=folder_exclude_mode,
            folder_priority=folder_priority,
            folder_priority_mode=folder_priority_mode,
        )
        if not groups:
            preview_stats["skip_unpreviewable"] = int(preview_stats["skip_unpreviewable"]) + 1
            logger.info("  候选%d：无法读取文件列表，跳过", index + 1)
            continue

        if folder_mode == "folder_diff":
            # 文件夹对比模式需要分享根目录，不能定位到单个子目录
            new_shareurl = _rewrite_shareurl_with_fid(shareurl, None)
        else:
            best_fid = _pick_fid_for_normal_mode(groups)
            new_shareurl = _rewrite_shareurl_with_fid(shareurl, best_fid)

        if not new_shareurl or new_shareurl == old_shareurl:
            logger.info("  候选%d：新旧链接相同", index + 1)
            continue

        task.shareurl = new_shareurl
        try:
            from app.services.task_share_links import drop_task_share_link

            drop_task_share_link(task)
        except Exception:  # pragma: no cover - 清理失败不影响换链
            logger.warning("自动换链：清理已保存的分享链接失败", exc_info=True)
        db.flush()
        logger.info("自动换链（死链修复）：成功 old=%s new=%s", old_shareurl, new_shareurl)
        return {
            "checked": True,
            "updated": True,
            "old_shareurl": old_shareurl,
            "new_shareurl": new_shareurl,
            "matched_taskname": str(suggestion.get("taskname") or suggestion.get("content") or "").strip(),
            "reason_detail": {"search": search_stats, "filter": filter_stats, "preview": preview_stats},
            "db_changed": True,
        }

    return {
        "checked": True,
        "updated": False,
        "reason": "no_better_candidate",
        "reason_detail": {"search": search_stats, "filter": filter_stats, "preview": preview_stats},
        "db_changed": bool(db_changed),
    }
