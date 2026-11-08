"""Legacy shim: new path app.application.drama_update_progress (S4-6, 2026-11-09). Remove one release later."""
from app.application.drama_update_progress import (  # noqa: F401
    logger,
    _parse_air_date,
    resolve_tmdb_latest_aired_episode,
    _to_int,
    build_drama_update_progress,
)
