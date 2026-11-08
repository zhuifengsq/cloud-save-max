"""Legacy shim: new path app.application.tmdb_view (S4-6, 2026-11-09). Remove one release later."""
from app.application.tmdb_view import (  # noqa: F401
    tmdb_cache_key,
    load_tmdb_status_map,
    load_tmdb_payload_map,
    get_tmdb_status,
)
