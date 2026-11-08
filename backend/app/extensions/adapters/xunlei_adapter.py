"""Legacy shim: new path app.infrastructure.drive.adapters.xunlei_adapter (S4-6, 2026-11-09). Remove one release later."""
from app.infrastructure.drive.adapters.xunlei_adapter import (  # noqa: F401
    logger,
    API_BASE,
    AUTH_BASE,
    CLIENT_ID,
    XUNLEI_UA,
    XUNLEI_HEADERS,
    DEVICE_ID,
    CAPTCHA_SIGN,
    CAPTCHA_TIMESTAMP,
    FILE_LIST_FILTERS,
    ERROR_CODES,
    _global_config_saver,
    _config_saver_factory,
    set_config_saver,
    XunleiAdapter,
)
