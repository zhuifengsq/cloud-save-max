"""S4-5 账号快照缓存护栏：命中/失效/隔离/灰度回退。

不连数据库：_snapshot_stamp 与 _load_rows 都是薄封装，patch 掉即可验证缓存语义。
"""
from __future__ import annotations

import pytest

from app.core.runtime.cache_version import drive_accounts_version, invalidate_drive_accounts
from app.core.settings import settings
from app.infrastructure.drive import account_manager as am


class FakeDb:
    def __init__(self) -> None:
        self.count = 1
        self.max_id = 1
        self.max_ts = "2026-11-09 00:00:00"


class _Row:
    def __init__(self, name: str = "acct-a") -> None:
        self.name = name
        self.drive_type = "quark"
        self.config_json = "{}"
        self.cookie = "cookie-value"
        self.enabled = True
        self.is_default = True


@pytest.fixture(autouse=True)
def _clean_cache():
    am.clear_account_snapshot_cache()
    yield
    am.clear_account_snapshot_cache()


@pytest.fixture
def wired(monkeypatch):
    """把 DB 访问换成计数器，返回 (db, loads)。"""
    db = FakeDb()
    loads = {"count": 0}

    def fake_load(_db):
        loads["count"] += 1
        return [_Row()]

    monkeypatch.setattr(am, "_snapshot_stamp", lambda _db: (db.count, db.max_id, db.max_ts))
    monkeypatch.setattr(am, "_load_rows", fake_load)
    monkeypatch.setattr(am, "_build_payload", lambda rows: {"accounts": [{"name": rows[0].name}]})
    monkeypatch.setattr(settings, "use_account_snapshot_cache", True)
    monkeypatch.setattr(settings, "account_snapshot_ttl_seconds", 30)
    return db, loads


def test_snapshot_is_cached_between_calls(wired):
    db, loads = wired
    first = am.load_accounts_payload(db)
    second = am.load_accounts_payload(db)
    assert first == second == {"accounts": [{"name": "acct-a"}]}
    assert loads["count"] == 1, "命中缓存时不应重复全表查询/解析"


def test_write_bump_invalidates_cache(wired):
    db, loads = wired
    am.load_accounts_payload(db)
    before = drive_accounts_version()
    assert invalidate_drive_accounts() > before
    am.load_accounts_payload(db)
    assert loads["count"] == 2


def test_table_shape_change_invalidates_cache(wired):
    db, loads = wired
    am.load_accounts_payload(db)
    db.count += 1  # 另一进程/脚本直接改库，版本号不知道，但指纹会变
    am.load_accounts_payload(db)
    assert loads["count"] == 2


def test_updated_at_change_invalidates_cache(wired):
    db, loads = wired
    am.load_accounts_payload(db)
    db.max_ts = "2026-11-09 00:00:05"
    am.load_accounts_payload(db)
    assert loads["count"] == 2


def test_cache_returns_deep_copy(wired):
    db, _loads = wired
    payload = am.load_accounts_payload(db)
    payload["accounts"][0]["name"] = "tampered"
    again = am.load_accounts_payload(db)
    assert again["accounts"][0]["name"] == "acct-a"


def test_flag_off_bypasses_cache(wired, monkeypatch):
    db, loads = wired
    monkeypatch.setattr(settings, "use_account_snapshot_cache", False)
    am.load_accounts_payload(db)
    am.load_accounts_payload(db)
    assert loads["count"] == 2


def test_clear_cache_forces_reload(wired):
    db, loads = wired
    am.load_accounts_payload(db)
    am.clear_account_snapshot_cache()
    am.load_accounts_payload(db)
    assert loads["count"] == 2


def test_database_account_manager_passes_payload_to_manager(monkeypatch):
    payload = {"accounts": [{"name": "acct-a"}]}
    calls: list[tuple[dict, bool]] = []

    class FakeAccountManager:
        def load_accounts(self, data, *, no_login: bool = False) -> None:
            calls.append((data, no_login))

    monkeypatch.setattr(am, "load_accounts_payload", lambda _db: payload)
    monkeypatch.setattr(am, "AccountManager", FakeAccountManager)
    manager = am.DatabaseAccountManager(object(), no_login=True)
    assert calls == [(payload, True)]
    assert isinstance(manager.manager, FakeAccountManager)


def test_adapter_throttle_uses_keyed_limiter(monkeypatch):
    from app.infrastructure.drive.adapters import base_adapter as ba
    from app.infrastructure.drive.adapters.quark_adapter import QuarkAdapter

    waits: list[tuple[str, float, float]] = []

    class FakeLimiter:
        def wait(self, key: str, min_interval: float, max_interval: float | None = None) -> float:
            waits.append((key, min_interval, max_interval))
            return 0.0

    monkeypatch.setattr(ba, "get_rate_limiter", lambda: FakeLimiter())
    monkeypatch.setattr(settings, "use_keyed_rate_limiter", True)
    adapter = QuarkAdapter(cookie="c", config={"cookie": "c"})
    adapter.account_name = "acct-a"
    adapter._throttle_request()
    adapter._throttle_request()
    assert waits == [("quark:acct-a", 0.05, 0.10), ("quark:acct-a", 0.05, 0.10)]


def test_adapter_throttle_falls_back_when_flag_off(monkeypatch):
    from app.infrastructure.drive.adapters import base_adapter as ba
    from app.infrastructure.drive.adapters.quark_adapter import QuarkAdapter

    calls: list[int] = []

    class FakeLimiter:
        def wait(self, *_args, **_kwargs) -> float:
            calls.append(1)
            return 0.0

    monkeypatch.setattr(ba, "get_rate_limiter", lambda: FakeLimiter())
    monkeypatch.setattr(settings, "use_keyed_rate_limiter", False)
    adapter = QuarkAdapter(cookie="c", config={"cookie": "c"})
    adapter._throttle_request()
    adapter._throttle_request()
    assert calls == [], "灰度关闭时应走旧的每实例限速，不碰 KeyedRateLimiter"
