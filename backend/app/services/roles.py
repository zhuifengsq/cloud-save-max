"""Legacy shim: new path app.application.roles (S4-6, 2026-11-09). Remove one release later."""
from app.application.roles import (  # noqa: F401
    list_roles,
    create_role,
    update_role,
    delete_role,
    set_role_permissions,
)
