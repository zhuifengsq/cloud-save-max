"""Legacy shim: new path app.application.invalid_share_links (S4-6, 2026-11-09). Remove one release later."""
from app.application.invalid_share_links import (  # noqa: F401
    upsert_invalid_share_link,
    list_invalid_shareurls,
    list_invalid_share_links,
    delete_invalid_share_link,
    clear_invalid_share_links,
)
