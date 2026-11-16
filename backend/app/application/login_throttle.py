"""Bounded single-process login admission; never hold the lock during authentication."""
from collections import deque
from hashlib import sha256
from contextlib import contextmanager
from dataclasses import dataclass, field
import logging
import math
from threading import Condition, Lock
import time
from typing import Callable, Iterator, TypeVar


logger = logging.getLogger(__name__)
Key = TypeVar('Key')


class LoginThrottled(Exception):
    def __init__(self, retry_after: float) -> None:
        self.retry_after = max(1, math.ceil(retry_after))


@dataclass
class _Record:
    failures: deque[float] = field(default_factory=deque)
    blocked_until: float = 0
    inflight: int = 0


@dataclass
class LoginAttempt:
    failed: bool = False


class LoginThrottle:
    def __init__(self, *, pair_limit: int = 5, ip_limit: int = 30,
                 window: int = 600, cooldown: int = 900, capacity: int = 10000,
                 sweep_interval: float = 30, wait_timeout: float = 2, max_waiters: int = 16,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if min(pair_limit, ip_limit, window, cooldown, capacity) <= 0:
            raise ValueError('Login throttle limits must be positive')
        self.pair_limit, self.ip_limit = pair_limit, ip_limit
        self.window, self.cooldown, self.capacity = window, cooldown, capacity
        self.clock = clock
        self._lock = Lock()
        self._condition = Condition(self._lock)
        if sweep_interval <= 0 or wait_timeout < 0 or max_waiters <= 0:
            raise ValueError('Invalid login admission maintenance/wait limits')
        self.sweep_interval, self.wait_timeout, self.max_waiters = sweep_interval, wait_timeout, max_waiters
        self._next_sweep = float('-inf')
        self._waiters = 0
        self._pairs: dict[tuple[str, str], _Record] = {}
        self._ips: dict[str, _Record] = {}
        self._next_capacity_log = float('-inf')

    def _expire(self, record: _Record, now: float) -> None:
        if record.blocked_until and record.blocked_until <= now:
            record.blocked_until = 0
            record.failures.clear()
        while record.failures and record.failures[0] <= now - self.window:
            record.failures.popleft()

    def _sweep(self, records: dict[Key, _Record], now: float) -> None:
        for key, record in list(records.items()):
            self._expire(record, now)
            if not record.inflight and not record.failures and record.blocked_until <= now:
                del records[key]

    @contextmanager
    def attempt(self, ip: str, username: str) -> Iterator[LoginAttempt]:
        key = (ip, sha256(username.encode('utf-8')).hexdigest())
        deadline = time.monotonic() + self.wait_timeout
        with self._lock:
            while True:
                now = self.clock()
                if now >= self._next_sweep:
                    self._sweep(self._pairs, now)
                    self._sweep(self._ips, now)
                    self._next_sweep = now + self.sweep_interval
                pair = self._pairs.get(key)
                host = self._ips.get(ip)
                for record in (pair, host):
                    if record is not None:
                        self._expire(record, now)
                        if record.blocked_until > now:
                            raise LoginThrottled(record.blocked_until - now)
                if pair is None or not pair.inflight:
                    break
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._waiters >= self.max_waiters:
                    raise LoginThrottled(1)
                self._waiters += 1
                try:
                    self._condition.wait(remaining)
                finally:
                    self._waiters -= 1
            if host is not None and len(host.failures) + host.inflight >= self.ip_limit:
                raise LoginThrottled(1)
            if ((pair is None and len(self._pairs) >= self.capacity) or
                    (host is None and len(self._ips) >= self.capacity)):
                if now >= self._next_capacity_log:
                    logger.warning('Login admission capacity exhausted')
                    self._next_capacity_log = now + 60
                raise LoginThrottled(60)
            pair = self._pairs.setdefault(key, _Record())
            host = self._ips.setdefault(ip, _Record())
            pair.inflight += 1
            host.inflight += 1
        attempt = LoginAttempt()
        completed = False
        try:
            yield attempt
            completed = True
        finally:
            with self._lock:
                now = self.clock()
                pair.inflight -= 1
                host.inflight -= 1
                if attempt.failed:
                    for record, limit in ((pair, self.pair_limit), (host, self.ip_limit)):
                        while record.failures and record.failures[0] <= now - self.window:
                            record.failures.popleft()
                        record.failures.append(now)
                        if len(record.failures) >= limit:
                            record.blocked_until = now + self.cooldown
                elif completed:
                    pair.failures.clear()
                self._condition.notify_all()
