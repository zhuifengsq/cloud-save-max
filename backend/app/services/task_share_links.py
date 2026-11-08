"""Legacy shim: new path app.application.task_share_links (S4-6, 2026-11-09). Remove one release later."""
from app.application.task_share_links import (  # noqa: F401
    SHARE_LINK_KEY,
    _load_extra,
    _store_extra,
    get_task_share_link,
    save_task_share_link,
    drop_task_share_link,
    merge_task_extra,
)
