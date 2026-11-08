"""Legacy shim: new path app.application.shareurl_update_logs (S4-6, 2026-11-09). Remove one release later."""
from app.application.shareurl_update_logs import (  # noqa: F401
    clear_drama_shareurl_update_logs,
    append_shareurl_update_log,
)
