from concurrent.futures import ThreadPoolExecutor

import pytest

from app.application.login_throttle import LoginThrottle, LoginThrottled


def test_pair_cooldown_and_monotonic_recovery():
    now = [0.0]
    guard = LoginThrottle(clock=lambda: now[0])
    for _ in range(5):
        with guard.attempt('ip', 'user') as attempt:
            attempt.failed = True
    with pytest.raises(LoginThrottled) as error:
        with guard.attempt('ip', 'user'):
            pass
    assert error.value.retry_after == 900
    now[0] = 901
    with guard.attempt('ip', 'user'):
        pass


def test_ip_failures_survive_pair_success():
    guard = LoginThrottle(ip_limit=3)
    for name in ['a', 'b', 'c']:
        with guard.attempt('ip', name) as attempt:
            attempt.failed = True
        if name != 'c':
            with guard.attempt('ip', name):
                pass
    with pytest.raises(LoginThrottled):
        with guard.attempt('ip', 'another'):
            pass


def test_capacity_never_evicts_bans_and_recovers_expired_records():
    now = [0.0]
    guard = LoginThrottle(pair_limit=1, capacity=1, clock=lambda: now[0])
    with guard.attempt('ip', 'a') as attempt:
        attempt.failed = True
    for key in [('other', 'b'), ('ip', 'a')]:
        with pytest.raises(LoginThrottled):
            with guard.attempt(*key):
                pass
    now[0] = 901
    with guard.attempt('other', 'b'):
        pass


def test_inflight_is_released_on_exception_and_other_pairs_are_independent():
    guard = LoginThrottle()
    with pytest.raises(RuntimeError):
        with guard.attempt('ip', 'a'):
            with ThreadPoolExecutor() as pool:
                assert pool.submit(lambda: _allowed(guard, 'other', 'b')).result(timeout=2)
            raise RuntimeError('database unavailable')
    assert _allowed(guard, 'ip', 'a')


def _allowed(guard, ip, name):
    with guard.attempt(ip, name):
        return True


def test_parallel_attempts_cannot_bypass_pair_reservation():
    guard = LoginThrottle()
    with guard.attempt('ip', 'a'):
        with pytest.raises(LoginThrottled):
            with guard.attempt('ip', 'a'):
                pass


def test_cooldown_expiry_recovers_even_with_longer_window():
    now = [0.0]
    guard = LoginThrottle(ip_limit=1, window=100, cooldown=10, clock=lambda: now[0])
    with guard.attempt('ip', 'a') as attempt:
        attempt.failed = True
    now[0] = 11
    assert _allowed(guard, 'ip', 'b')
