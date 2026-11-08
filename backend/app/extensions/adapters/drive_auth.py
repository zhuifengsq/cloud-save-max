"""Legacy shim: new path app.infrastructure.drive.adapters.drive_auth (S4-6, 2026-11-09). Remove one release later."""
from app.infrastructure.drive.adapters.drive_auth import (  # noqa: F401
    AuthMethod,
    DriveAuthRequired,
)
