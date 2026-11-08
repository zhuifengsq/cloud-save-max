"""Legacy shim: new path app.application.drama_share_autoupdate (S4-6, 2026-11-09). Remove one release later."""
from app.application.drama_share_autoupdate import (  # noqa: F401
    logger,
    MAX_PROBE_CANDIDATES,
    _task_extra,
    _task_addition,
    _is_auto_update_enabled,
    _pick_drive_type,
    is_auto_update_task,
    is_115_auto_update_task,
    _dedupe_preserve_order,
    _rewrite_shareurl_with_fid,
    _search_candidates,
    _prepare_candidates,
    _pick_fid_for_normal_mode,
    resolve_drama_shareurl_update,
)
