"""Legacy shim: new path app.application.generated_share_check (S4-6, 2026-11-09). Remove one release later."""
from app.application.generated_share_check import (  # noqa: F401
    logger,
    SNAPSHOT_KEY,
    FAILURE_STAGE,
    read_share_check,
    _map_kind,
    _failure,
    _probe,
    check_generated_share,
)
