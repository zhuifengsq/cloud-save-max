"""Legacy shim: new path app.infrastructure.drive.factory (S4-6, 2026-11-09). Remove one release later."""
from app.infrastructure.drive.factory import (  # noqa: F401
    logger,
    AdapterFactory,
    AccountManager,
)
