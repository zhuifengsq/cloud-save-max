"""Legacy shim: new path app.application.tmdb_settings (S4-6, 2026-11-09). Remove one release later."""
from app.application.tmdb_settings import (  # noqa: F401
    DEFAULT_TMDB_SETTING_ID,
    _TMDB_LANG_PAIR_SESSION_KEY,
    get_or_create_tmdb_setting,
    get_tmdb_lang_pair,
    invalidate_tmdb_lang_pair_cache,
    _load_json,
    load_tmdb_config,
    get_tmdb_runtime_config,
    update_tmdb_setting,
)
