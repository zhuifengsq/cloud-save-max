"""Legacy shim: new path app.application.telegram_bot.__init__ (S4-6, 2026-11-09). Remove one release later."""
from app.application.telegram_bot.__init__ import (  # noqa: F401
    __all__,
)
