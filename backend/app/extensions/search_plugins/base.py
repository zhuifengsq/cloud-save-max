"""Legacy shim: new path app.infrastructure.search.base (S4-6, 2026-11-09). Remove one release later."""
from app.infrastructure.search.base import (  # noqa: F401
    BaseSearchPlugin,
)
