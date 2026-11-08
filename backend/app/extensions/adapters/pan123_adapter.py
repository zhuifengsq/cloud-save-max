"""Legacy shim: new path app.infrastructure.drive.adapters.pan123_adapter (S4-6, 2026-11-09). Remove one release later."""
from app.infrastructure.drive.adapters.pan123_adapter import (  # noqa: F401
    _global_config_saver,
    logger,
    set_config_saver,
    Pan123Adapter,
)
