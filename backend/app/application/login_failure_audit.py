"""Bounded, best-effort failure aggregation; request threads never write audits."""
from __future__ import annotations

import logging
from threading import Event, Lock, Thread
import time
from typing import Callable

logger = logging.getLogger(__name__)
FailureBatch = list[tuple[str, int]]


class LoginFailureAudit:
    def __init__(self, *, writer: Callable[[FailureBatch], None], capacity: int = 1000,
                 interval: float = 5, autostart: bool = True,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if capacity <= 0 or interval <= 0:
            raise ValueError('Audit capacity and interval must be positive')
        self.writer, self.capacity, self.interval = writer, capacity, interval
        self.autostart = autostart
        self.clock = clock
        self._pending: dict[str, int] = {}
        self._lock = Lock()
        self._flush_lock = Lock()
        self._stop = Event()
        self._thread: Thread | None = None
        self._next_diagnostic = float('-inf')
        self._next_start = float('-inf')

    @property
    def closed(self) -> bool:
        return self._stop.is_set()

    def record(self, ip: str) -> bool:
        with self._lock:
            if self._stop.is_set():
                return False
            if ip not in self._pending and len(self._pending) >= self.capacity:
                self._diagnose('Login failure audit capacity exhausted; aggregate dropped')
                return False
            self._pending[ip] = min(1_000_000_000, self._pending.get(ip, 0) + 1)
            if self.autostart and self._thread is None and self.clock() >= self._next_start:
                self._thread = Thread(target=self._consume, name='login-failure-audit', daemon=True)
                try:
                    self._thread.start()
                except Exception:
                    self._thread = None
                    self._next_start = self.clock() + self.interval
                    self._diagnose('Login failure audit worker start failed; retry deferred')
        return True

    def _diagnose(self, message: str) -> None:
        now = self.clock()
        if now >= self._next_diagnostic:
            logger.warning(message)
            self._next_diagnostic = now + 60

    def flush(self) -> bool:
        with self._flush_lock:
            with self._lock:
                batch = list(self._pending.items())
                self._pending.clear()
            if not batch:
                return True
            try:
                self.writer(batch)
                return True
            except Exception:
                with self._lock:
                    self._diagnose('Login failure audit batch failed; aggregate dropped')
                return False

    def _consume(self) -> None:
        while not self._stop.wait(self.interval):
            self.flush()
        self.flush()

    def close(self, timeout: float = 2) -> None:
        with self._lock:
            self._stop.set()
            thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                logger.warning('Login failure audit shutdown timed out; pending aggregates may be lost')
        elif self._pending:
            if not self.autostart:
                self.flush()
            else:
                logger.warning('Login failure audit stopped without a worker; pending aggregates dropped')
                with self._lock:
                    self._pending.clear()


