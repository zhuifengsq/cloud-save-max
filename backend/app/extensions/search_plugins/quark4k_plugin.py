"""Legacy shim: new path app.infrastructure.search.quark4k_plugin (S4-6, 2026-11-09). Remove one release later."""
from app.infrastructure.search.quark4k_plugin import (  # noqa: F401
    LINK_RE,
    _clean_text,
    _PostParser,
    Quark4KPlugin,
)
