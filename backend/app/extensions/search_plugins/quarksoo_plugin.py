"""Legacy shim: new path app.infrastructure.search.quarksoo_plugin (S4-6, 2026-11-09). Remove one release later."""
from app.infrastructure.search.quarksoo_plugin import (  # noqa: F401
    logger,
    SEARCH_URL,
    USER_AGENT,
    HEADERS,
    MAX_RESOLVE,
    ROW_RE,
    TAG_RE,
    LEADING_NOISE_RE,
    SPACE_RE,
    REDIRECT_CODES,
    _fetch_page,
    _clean_title,
    _matches,
    _redirect_target,
    _resolve,
    QuarkSooPlugin,
)
