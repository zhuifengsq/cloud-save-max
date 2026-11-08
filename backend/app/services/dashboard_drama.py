"""Legacy shim: new path app.application.dashboard_drama (S4-6, 2026-11-09). Remove one release later."""
from app.application.dashboard_drama import (  # noqa: F401
    _safe_json_object,
    _normalize_weekdays,
    _daterange,
    build_drama_overview,
)
