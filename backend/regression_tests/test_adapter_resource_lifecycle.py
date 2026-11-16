import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.infrastructure.drive.factory import AdapterFactory, AccountManager


class Adapter:
    DRIVE_TYPE = 'lifecycle'
    instances = []

    @classmethod
    def resolve_runtime_config(cls, config=None, cookie=''):
        return config or {'cookie': cookie}

    def __init__(self, **kwargs):
        self.closes = 0
        self.is_active = True
        self.instances.append(self)

    def close(self):
        self.closes += 1

    def operation(self, started=None, finish=None):
        if started:
            started.set()
            assert finish.wait(5)
        assert self.closes == 0
        return 'ok'


@pytest.fixture(autouse=True)
def cache(monkeypatch):
    AdapterFactory.clear_cache()
    monkeypatch.setitem(AdapterFactory.ADAPTER_MAP, 'lifecycle', Adapter)
    monkeypatch.setattr(AdapterFactory, '_instance_cache_max', 1)
    Adapter.instances = []
    yield
    AdapterFactory.clear_cache()


def test_clear_closes_after_last_borrower_releases():
    first = AdapterFactory.create_adapter('lifecycle')
    second = AdapterFactory.create_adapter('lifecycle')
    owned = Adapter.instances[0]
    assert len(Adapter.instances) == 1
    AdapterFactory.clear_cache()
    assert owned.closes == 0
    assert second.operation() == 'ok'
    first.close()
    assert owned.closes == 0
    second.close()
    second.close()
    assert owned.closes == 1


def test_eviction_waits_for_borrower_and_closes_idle_entry():
    first = AdapterFactory.create_adapter('lifecycle', 'first')
    owned = Adapter.instances[0]
    second = AdapterFactory.create_adapter('lifecycle', 'second')
    assert owned.closes == 0
    assert first.operation() == 'ok'
    first.close()
    assert owned.closes == 1
    second.close()
    assert Adapter.instances[1].closes == 0
    AdapterFactory.clear_cache()
    assert Adapter.instances[1].closes == 1


def test_close_during_running_method_waits_for_method_return():
    lease = AdapterFactory.create_adapter('lifecycle')
    owned = Adapter.instances[0]
    started, finish = threading.Event(), threading.Event()
    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(lease.operation, started, finish)
        assert started.wait(5)
        try:
            AdapterFactory.clear_cache()
            lease.close()
            assert owned.closes == 0
        finally:
            finish.set()
        assert result.result() == 'ok'
    assert owned.closes == 1
    with pytest.raises(RuntimeError, match='released'):
        lease.operation()


def test_manager_releases_all_accounts_on_close_and_reload():
    manager = AccountManager()
    payload = {'accounts': [{'drive_type': 'lifecycle', 'name': 'one'}]}
    manager.load_accounts(payload)
    old = Adapter.instances[0]
    AdapterFactory.clear_cache()
    manager.load_accounts(payload)
    assert old.closes == 1
    current = Adapter.instances[1]
    AdapterFactory.clear_cache()
    manager.close()
    manager.close()
    assert current.closes == 1


def test_clear_during_constructor_does_not_repopulate_old_generation(monkeypatch):
    started, finish = threading.Event(), threading.Event()

    class SlowAdapter(Adapter):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            started.set()
            assert finish.wait(5)

    monkeypatch.setitem(AdapterFactory.ADAPTER_MAP, 'lifecycle', SlowAdapter)
    with ThreadPoolExecutor(1) as pool:
        result = pool.submit(AdapterFactory.create_adapter, 'lifecycle')
        assert started.wait(5)
        try:
            AdapterFactory.clear_cache()
        finally:
            finish.set()
        lease = result.result()
    assert not AdapterFactory._instance_cache
    owned = Adapter.instances[0]
    assert owned.closes == 0
    assert lease.operation() == 'ok'
    lease.close()
    assert owned.closes == 1


def test_scope_releases_borrowers_on_exception():
    from app.infrastructure.drive.lifecycle import adapter_scope

    with pytest.raises(ValueError):
        with adapter_scope():
            lease = AdapterFactory.create_adapter('lifecycle')
            AdapterFactory.clear_cache()
            assert lease.operation() == 'ok'
            raise ValueError('operation failed')
    assert Adapter.instances[0].closes == 1


def test_all_registered_adapters_close_their_real_owned_transports(monkeypatch):
    import requests
    from unittest.mock import Mock

    # No credentials or network: real adapters construct real local Session objects.
    monkeypatch.setattr(requests.sessions.Session, 'request', lambda *a, **kw: pytest.fail('network forbidden'))
    for drive_type, adapter_class in AdapterFactory.ADAPTER_MAP.items():
        if drive_type == 'lifecycle':
            continue
        adapter = adapter_class(no_login=True)
        transports = [getattr(adapter, name, None) for name in ('_session', 'auth_session')]
        client = getattr(adapter, '_client', None)
        if client is not None:
            transports.append(client._session)
        closes = []
        for transport in transports:
            if transport is not None:
                close = Mock(wraps=transport.close)
                monkeypatch.setattr(transport, 'close', close)
                closes.append(close)
        adapter.close()
        adapter.close()
        for close in closes:
            close.assert_called_once()


def test_asgi_scope_includes_response_body_and_background_work():
    import asyncio
    from app.infrastructure.drive.lifecycle import AdapterLeaseMiddleware

    async def application(scope, receive, send):
        lease = AdapterFactory.create_adapter('lifecycle')
        AdapterFactory.clear_cache()
        await send({'type': 'http.response.start', 'status': 200})
        await send({'type': 'http.response.body', 'body': b'ok'})
        assert lease.operation() == 'ok'  # Response background work still holds its borrow.

    async def send(message):
        assert Adapter.instances[0].closes == 0

    asyncio.run(AdapterLeaseMiddleware(application)({'type': 'http'}, None, send))
    assert Adapter.instances[0].closes == 1


def test_legacy_borrower_finalizer_safely_releases_after_eviction():
    import gc
    lease = AdapterFactory.create_adapter('lifecycle')
    owned = Adapter.instances[0]
    AdapterFactory.clear_cache()
    assert owned.closes == 0
    del lease
    gc.collect()
    assert owned.closes == 1


def test_shutdown_closes_idle_cache_and_retires_late_constructions():
    lease = AdapterFactory.create_adapter('lifecycle')
    lease.close()
    AdapterFactory.shutdown()
    try:
        assert Adapter.instances[0].closes == 1
        later = AdapterFactory.create_adapter('lifecycle')
        assert later.operation() == 'ok'
        assert not AdapterFactory._instance_cache
        later.close()
        assert Adapter.instances[1].closes == 1
    finally:
        AdapterFactory.startup()


def test_retained_borrow_survives_request_scope_and_releases_explicitly():
    from app.infrastructure.drive.lifecycle import adapter_scope
    with adapter_scope():
        request_lease = AdapterFactory.create_adapter('lifecycle')
        retained = request_lease.retain()
        AdapterFactory.clear_cache()
    assert retained.operation() == 'ok'
    assert Adapter.instances[0].closes == 0
    retained.close()
    assert Adapter.instances[0].closes == 1
