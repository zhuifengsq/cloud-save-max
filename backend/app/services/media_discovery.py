"""Legacy shim: new path app.application.media_discovery (S4-6, 2026-11-09). Remove one release later."""
from app.application.media_discovery import (  # noqa: F401
    _douban_client,
    _match_cache,
    _executor,
    MediaType,
    list_douban_categories,
    _get_media_type,
    fetch_douban_list,
    _pick_best_by_year,
    _to_tmdb_brief,
    _match_one,
    enrich_douban_items,
    tmdb_search,
    tmdb_detail,
    tmdb_credits,
    _infer_tv_update_weekdays,
)
