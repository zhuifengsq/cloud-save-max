"""Legacy shim: new path app.application.setup (S4-6, 2026-11-09). Remove one release later."""
from app.application.setup import (  # noqa: F401
    is_initialized,
    seed_permissions,
    ensure_permissions_and_roles,
    create_initial_admin,
)
