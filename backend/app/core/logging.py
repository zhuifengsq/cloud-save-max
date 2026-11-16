from __future__ import annotations

import logging
import logging.config
import os
import sys
import copy
import traceback
from types import TracebackType
from typing import Any
from app.core.log_redaction import redact_log_text


_configured = False


def _safe_argument(value: object) -> object:
    if isinstance(value, BaseException):
        return type(value).__name__
    return value


class SafeFormatter(logging.Formatter):
    def formatException(self, ei: tuple[type[BaseException], BaseException, TracebackType | None] | tuple[None, None, None]) -> str:
        # Keep stack locations and exception type; exception text and source lines
        # can contain opaque cookies which cannot be reliably recognized by regex.
        if ei[0] is None:
            return ''
        frames = traceback.extract_tb(ei[2])
        lines = [f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}' for frame in frames]
        return '\n'.join(['Traceback (most recent call last):', *lines, ei[0].__name__])

    def format(self, record: logging.LogRecord) -> str:
        safe = copy.copy(record)
        if isinstance(safe.msg, BaseException):
            safe.msg = type(safe.msg).__name__
        if isinstance(safe.args, tuple):
            safe.args = tuple(_safe_argument(value) for value in safe.args)
        elif isinstance(safe.args, dict):
            safe.args = {key: _safe_argument(value) for key, value in safe.args.items()}
        safe.exc_text = None
        return redact_log_text(super().format(safe))


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _resolve_log_level() -> str:
    level = (os.getenv("LOG_LEVEL") or "").strip().upper()
    if level:
        return level
    if _env_bool("DEBUG", default=False):
        return "DEBUG"
    return "INFO"


def setup_logging(*, force: bool = False) -> None:
    global _configured
    if _configured and not force:
        return

    level = _resolve_log_level()

    config: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": SafeFormatter,
                "format": "%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stdout",
            }
        },
        "root": {"level": level, "handlers": ["console"]},
        "loggers": {
            "uvicorn": {"level": level, "handlers": ["console"], "propagate": False},
            "uvicorn.error": {"level": level, "handlers": ["console"], "propagate": False},
            "uvicorn.access": {"level": level, "handlers": ["console"], "propagate": False},
            "app.access": {"level": level, "handlers": ["console"], "propagate": False},
            "rebulk": {"level": "INFO", "handlers": ["console"], "propagate": False},
        },
    }

    logging.config.dictConfig(config)
    logging.captureWarnings(True)

    for stream in (sys.stdout, sys.stderr):
        try:
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(line_buffering=True)
        except Exception:
            pass

    _configured = True

