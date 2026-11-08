"""Legacy shim: new path app.infrastructure.drive.adapters.baidu_adapter (S4-6, 2026-11-09). Remove one release later."""
from app.infrastructure.drive.adapters.baidu_adapter import (  # noqa: F401
    logger,
    PCS_BAIDU_COM,
    PAN_BAIDU_COM,
    PCS_UA,
    PAN_UA,
    PCS_HEADERS,
    PAN_HEADERS,
    PCS_APP_ID,
    PAN_APP_ID,
    _calu_md5,
    _now_timestamp,
    _dump_json,
    PcsNode,
    PanNode,
    BaiduAdapter,
)
