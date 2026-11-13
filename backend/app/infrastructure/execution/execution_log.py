from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable
from app.core.settings import settings


_TRUNCATED = '\n… 日志已截断，保留开头与末尾 …\n'.encode('utf-8')


def _prefix(data: bytes, size: int) -> bytes:
    return data[:max(0, size)].decode('utf-8', errors='ignore').encode('utf-8')


def _suffix(data: bytes, size: int) -> bytes:
    if size <= 0:
        return b''
    return data[-size:].decode('utf-8', errors='ignore').encode('utf-8')


def bounded_text(text: str, limit: int) -> tuple[str, bool]:
    """Bound a display value in UTF-8 bytes; never use it as business input."""
    data = text.encode('utf-8')
    if len(data) <= limit:
        return text, False
    marker = _prefix(_TRUNCATED, limit)
    budget = max(0, limit - len(marker))
    return (_prefix(data, budget // 2) + marker + _suffix(data, budget - budget // 2)).decode('utf-8'), True


@dataclass
class ExecutionLog:
    max_bytes: int = field(default_factory=lambda: settings.execution_log_max_bytes)
    stage: str | None = None
    started_at: datetime = field(default_factory=lambda: datetime.now())
    emit_line: Callable[[str], None] | None = field(default=None, repr=False)
    emit_stage: Callable[[str], None] | None = field(default=None, repr=False)
    emit_progress: Callable[[dict[str, Any]], None] | None = field(default=None, repr=False)
    _buffer: bytes = field(default=b'', init=False, repr=False)
    _head: bytes = field(default=b'', init=False, repr=False)
    _tail: bytes = field(default=b'', init=False, repr=False)
    _input_bytes: int = field(default=0, init=False, repr=False)
    truncated: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.max_bytes < 1:
            raise ValueError('max_bytes must be positive')

    @property
    def lines(self) -> list[str]:
        """Read-only compatibility view; retained storage itself is byte-bounded."""
        return self.render().splitlines()

    @property
    def retained_bytes(self) -> int:
        return len(self._buffer) + len(self._head) + len(self._tail)

    @property
    def dropped_bytes(self) -> int:
        return max(0, self._input_bytes - self.retained_bytes)

    def metadata(self) -> dict[str, Any]:
        return {'truncated': self.truncated, 'dropped_bytes': self.dropped_bytes, 'max_bytes': self.max_bytes}

    def _append(self, line: str) -> None:
        data = (line + '\n').encode('utf-8')
        self._input_bytes += len(data)
        if not self.truncated and len(self._buffer) + len(data) <= self.max_bytes:
            self._buffer += data
            return
        marker = _prefix(_TRUNCATED, self.max_bytes)
        budget = self.max_bytes - len(marker)
        head_size, tail_size = budget // 2, budget - budget // 2
        if not self.truncated:
            self._head = _prefix((self._buffer + data[:head_size])[:head_size], head_size)
            self._tail = _suffix(self._buffer, tail_size)
            self._buffer = b''
            self.truncated = True
        # Never concatenate the complete incoming line with retained data.
        self._tail = _suffix(self._tail + data[-tail_size:] if tail_size else b'', tail_size)

    def set_stage(self, stage: str | None) -> None:
        self.stage = stage
        if self.emit_stage and stage:
            self.emit_stage(str(stage))

    def section(self, title: str) -> None:
        title = str(title or "").strip() or "阶段"
        line = f"==============={title}==============="
        self._append(line)
        if self.emit_line:
            self.emit_line(line)

    def line(self, text: str = "") -> None:
        line = str(text)
        self._append(line)
        if self.emit_line:
            self.emit_line(line)

    def progress(self, payload: dict[str, Any]) -> None:
        if self.emit_progress:
            self.emit_progress(dict(payload or {}))

    def render(self) -> str:
        if not self.truncated:
            return self._buffer.decode('utf-8').rstrip() + '\n'
        marker = _prefix(_TRUNCATED, self.max_bytes)
        return (self._head + marker + self._tail).decode('utf-8')
