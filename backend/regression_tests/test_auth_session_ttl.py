# -*- coding: utf-8 -*-
"""扫码登录会话的内存回收（S4 · 方案 §4.2 第 5 条）。

旧实现只在 get_auth_session 命中时删过期项：用户放弃扫码后会话再也不会被访问，
字典与其持有的 adapter 实例（登录态/连接）就永久驻留。本测试锁两点：
1. 新建会话时顺手清理过期会话；
2. 会话数有上限，不会无界增长。
"""
from __future__ import annotations

import pytest

from app.application import drive_account_auth_sessions as sessions


@pytest.fixture(autouse=True)
def _cleanup():
    with sessions._LOCK:
        sessions._SESSIONS.clear()
    yield
    with sessions._LOCK:
        sessions._SESSIONS.clear()


def _create(ttl_seconds: int):
    return sessions.create_auth_session(
        account_id=1,
        drive_type="quark",
        method="qrcode",
        adapter=object(),
        payload={"k": "v"},
        ttl_seconds=ttl_seconds,
    )


def test_expired_session_is_swept_on_next_create():
    stale = _create(ttl_seconds=0)
    assert sessions.get_auth_session(stale.session_id) is None  # 命中即删（旧行为保留）

    another_stale = _create(ttl_seconds=0)
    fresh = _create(ttl_seconds=600)

    alive = {another_stale.session_id, fresh.session_id}
    assert set(sessions._SESSIONS) <= alive
    # 过期会话不因为「没被访问」而残留
    assert another_stale.session_id not in sessions._SESSIONS
    assert sessions.get_auth_session(fresh.session_id) is not None


def test_session_store_is_bounded():
    for _ in range(sessions._MAX_SESSIONS + 20):
        _create(ttl_seconds=600)
    assert len(sessions._SESSIONS) <= sessions._MAX_SESSIONS
    # 淘汰的是最早到期的一批，最新会话必须还在
    newest = _create(ttl_seconds=600)
    assert sessions.get_auth_session(newest.session_id) is not None


def test_expired_session_adapter_is_released():
    adapter = object()
    stale = sessions.create_auth_session(
        account_id=1,
        drive_type="quark",
        method="qrcode",
        adapter=adapter,
        payload={},
        ttl_seconds=0,
    )
    assert sessions._SESSIONS[stale.session_id].adapter is adapter
    _create(ttl_seconds=60)
    assert stale.session_id not in sessions._SESSIONS
