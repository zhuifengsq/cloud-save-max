"""Legacy shim: new path app.application.permissions (S4-6, 2026-11-09). Remove one release later."""
from app.application.permissions import (  # noqa: F401
    list_permissions,
)
