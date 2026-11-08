"""Legacy shim: new path app.application.audit (S4-6, 2026-11-09). Remove one release later."""
from app.application.audit import (  # noqa: F401
    write_audit_log,
)
