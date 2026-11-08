"""Legacy shim: new path app.application.audit_logs (S4-6, 2026-11-09). Remove one release later."""
from app.application.audit_logs import (  # noqa: F401
    list_audit_logs,
)
