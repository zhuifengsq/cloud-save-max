"""Legacy shim: new path app.infrastructure.drive.adapters.guangya_adapter (S4-6, 2026-11-09). Remove one release later."""
from app.infrastructure.drive.adapters.guangya_adapter import (  # noqa: F401
    logger,
    generate_did,
    generate_traceparent,
    _deep_get,
    _extract_message,
    _extract_data,
    _extract_items,
    _to_bool,
    _to_int,
    _clean_text,
    _is_success,
    _has_explicit_failure,
    _payload_summary,
    GuangyaClientLite,
    GuangyaAdapter,
)
