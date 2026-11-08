"""Legacy shim: new path app.application.notifications.task_notify (S4-6, 2026-11-09). Remove one release later."""
from app.application.notifications.task_notify import (  # noqa: F401
    DRAMA_NOTIFY_TITLE,
    _normalize_savepath,
    extract_root_new_files,
    format_root_file_tree,
    build_share_check_section,
    build_task_section,
    _build_transfer_section,
)
