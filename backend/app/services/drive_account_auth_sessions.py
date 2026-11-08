"""Legacy shim: new path app.application.drive_account_auth_sessions (S4-6, 2026-11-09). Remove one release later."""
from app.application.drive_account_auth_sessions import (  # noqa: F401
    DriveAccountAuthSession,
    _LOCK,
    _SESSIONS,
    _MAX_SESSIONS,
    _purge_expired_locked,
    create_auth_session,
    get_auth_session,
    delete_auth_session,
)
