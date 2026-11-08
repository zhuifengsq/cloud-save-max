"""Legacy shim: new path app.infrastructure.drive.adapters.cloud189_adapter (S4-6, 2026-11-09). Remove one release later."""
from app.infrastructure.drive.adapters.cloud189_adapter import (  # noqa: F401
    logger,
    _global_config_saver,
    set_config_saver,
    Cloud189CaptchaRequired,
    Cloud189SecondValidRequired,
    Cloud189Adapter,
)
