"""Legacy shim: new path app.application.users (S4-6, 2026-11-09). Remove one release later."""
from app.application.users import (  # noqa: F401
    list_users,
    create_user,
    set_user_status,
    set_user_roles,
    batch_set_status,
    batch_set_roles,
)
