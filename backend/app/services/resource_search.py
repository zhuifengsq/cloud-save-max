"""Legacy shim: new path app.application.resource_search (S4-6, 2026-11-09). Remove one release later."""
from app.application.resource_search import (  # noqa: F401
    logger,
    _search_cache_ttl,
    _search_cache,
    _search_cache_lock,
    _get_search_cache_ttl,
    _update_search_cache_ttl,
    cache_clear,
    _normalize_drive_type,
    extract_quark_url,
    get_quark_share_author,
    enrich_suggestions_with_authors,
    filter_blocked_sharers,
    fetch_task_suggestions,
)
