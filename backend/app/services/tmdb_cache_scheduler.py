"""Legacy shim: new path app.application.tmdb_cache_scheduler (S4-6, 2026-11-09). Remove one release later."""
from app.application.tmdb_cache_scheduler import (  # noqa: F401
    DEFAULT_TMDB_CACHE_SCHEDULER_SETTING_ID,
    load_tmdb_cache_scheduler_setting,
    get_or_create_tmdb_cache_scheduler_setting,
    update_tmdb_cache_scheduler_setting,
)
