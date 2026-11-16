from __future__ import annotations

"""Reference-counted cache ownership and explicit operation scopes for adapters."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import logging
import threading
import weakref
from typing import Any, Callable, Iterator, ParamSpec, Self, TypeVar
from types import TracebackType
from starlette.types import ASGIApp, Scope, Receive, Send
from app.infrastructure.drive.adapters.base_adapter import BaseCloudDriveAdapter
from app.core.drive_auth import DriveAuthRequired

P = ParamSpec("P")
R = TypeVar("R")

logger = logging.getLogger(__name__)
_scope: ContextVar[list[AdapterLease] | None] = ContextVar('adapter_lease_scope', default=None)


class AdapterResource:
    """One cache owner plus any number of independent borrower leases."""

    def __init__(self, adapter: BaseCloudDriveAdapter) -> None:
        self.adapter = adapter
        self.lock = threading.RLock()
        self.borrowers = 0
        self.retired = False
        self.closed = False

    def acquire(self, *, track_scope: bool = True) -> AdapterLease:
        with self.lock:
            if self.closed:
                raise RuntimeError('Adapter resource has been closed')
            self.borrowers += 1
        return AdapterLease(self, track_scope=track_scope)

    def release(self) -> None:
        with self.lock:
            self.borrowers -= 1
        self._close_if_unused()

    def retire(self) -> None:
        with self.lock:
            self.retired = True
        self._close_if_unused()

    def _close_if_unused(self) -> None:
        with self.lock:
            if not self.retired or self.borrowers or self.closed:
                return
            self.closed = True
        try:
            close = getattr(self.adapter, 'close', None)
            if callable(close):
                close()
        except Exception:
            logger.warning('Adapter resource close failed', exc_info=True)


class AdapterLease:
    """Transparent adapter view. close() releases this borrow, never other callers."""

    _resource: AdapterResource
    _guard: threading.RLock
    _finalizer: weakref.finalize[[], None]

    def __init__(self, resource: AdapterResource, *, track_scope: bool = True) -> None:
        object.__setattr__(self, '_resource', resource)
        object.__setattr__(self, '_guard', threading.RLock())
        object.__setattr__(self, '_finalizer', weakref.finalize(self, resource.release))
        scope = _scope.get()
        if track_scope and scope is not None:
            scope.append(self)

    def _fork(self) -> AdapterLease:
        with self._guard:
            self._check_open()
            return self._resource.acquire()

    def retain(self) -> AdapterLease:
        """Create an unscoped borrow for an explicit longer-lived owner (auth challenge)."""
        with self._guard:
            self._check_open()
            return self._resource.acquire(track_scope=False)

    def _check_open(self) -> None:
        if not self._finalizer.alive:
            raise RuntimeError('Adapter lease has been released')

    def close(self) -> None:
        with self._guard:
            self._finalizer()

    def __getattr__(self, name: str) -> Any:
        with self._guard:
            self._check_open()
            value = getattr(self._resource.adapter, name)
        if not callable(value):
            return value

        @wraps(value)
        def call(*args: Any, **kwargs: Any) -> Any:
            # Pin the resource for the entire operation if this lease is concurrently closed.
            with self._guard:
                self._check_open()
                pin = self._resource.acquire(track_scope=False)
            try:
                return value(*args, **kwargs)
            except DriveAuthRequired as exc:
                # Adapter implementations raise with adapter=self. Keep the lease
                # boundary intact so challenge owners can retain this transport.
                if exc.adapter is self._resource.adapter:
                    exc.adapter = self
                raise
            finally:
                pin.close()
        return call

    def __setattr__(self, name: str, value: Any) -> None:
        with self._guard:
            self._check_open()
            setattr(self._resource.adapter, name, value)

    def __enter__(self) -> Self:
        self._check_open()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None) -> None:
        self.close()


@contextmanager
def adapter_scope() -> Iterator[None]:
    """Release all leases created in this operation, also on cancellation/errors."""
    leases: list[AdapterLease] = []
    token = _scope.set(leases)
    try:
        yield
    finally:
        _scope.reset(token)
        for lease in reversed(leases):
            lease.close()


def scoped_adapters(function: Callable[P, R]) -> Callable[P, R]:
    @wraps(function)
    def run(*args: P.args, **kwargs: P.kwargs) -> R:
        with adapter_scope():
            return function(*args, **kwargs)
    return run


class AdapterLeaseMiddleware:
    """Keep HTTP/stream/background-response borrowers until the whole ASGI call ends."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        with adapter_scope():
            await self.app(scope, receive, send)


