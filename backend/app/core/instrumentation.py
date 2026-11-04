"""请求级诊断埋点（SQL 次数/耗时、外部 HTTP 次数/耗时）。

实现方式：
- 每个 HTTP 请求由 TimingMiddleware 绑定一个 RequestScope（contextvar）；
- SQLAlchemy 的 before/after_cursor_execute 事件只在该 contextvar 存在时累加；
- requests.Session.request 打一次补丁，同样只在 contextvar 存在时累加。

contextvar 会随 anyio 的线程池调用被复制到工作线程（同步端点跑在线程池里），
所以同步端点内部的 SQL / requests 调用也能统计到；后台线程（例如任务执行线程、
SSE 的 worker 线程）不在请求上下文里，不会污染请求统计。
"""

from __future__ import annotations

import functools
import time
from contextvars import ContextVar

from sqlalchemy import event
from sqlalchemy.engine import Engine


class RequestScope:
    """一次 HTTP 请求内的诊断计数。"""

    __slots__ = ("sql_count", "sql_ms", "http_count", "http_ms")

    def __init__(self) -> None:
        self.sql_count = 0
        self.sql_ms = 0.0
        self.http_count = 0
        self.http_ms = 0.0


_scope: ContextVar[RequestScope | None] = ContextVar("app_request_scope", default=None)
_sql_started: ContextVar[float | None] = ContextVar("app_sql_started", default=None)


def current_scope() -> RequestScope | None:
    return _scope.get()


def bind_scope(scope: RequestScope):
    return _scope.set(scope)


def reset_scope(token) -> None:
    _scope.reset(token)


def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany) -> None:
    if _scope.get() is None:
        return
    _sql_started.set(time.perf_counter())


def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany) -> None:
    scope = _scope.get()
    if scope is None:
        return
    started = _sql_started.get()
    if started is None:
        return
    scope.sql_count += 1
    scope.sql_ms += (time.perf_counter() - started) * 1000.0


# 类级监听：对所有 Engine 生效，不需要逐个 engine 注册。
event.listen(Engine, "before_cursor_execute", _before_cursor_execute)
event.listen(Engine, "after_cursor_execute", _after_cursor_execute)


def _patch_requests() -> None:
    """给 requests 打补丁：统计请求上下文内的外部 HTTP 调用。

    各网盘适配器有的直接用 requests.get/post，有的持有 Session，
    统一拦 Session.request 可以覆盖两类用法；补丁只装一次。
    """
    try:
        import requests.sessions as requests_sessions
    except Exception:  # pragma: no cover - requests 是硬依赖，理论上不会缺
        return
    if getattr(requests_sessions.Session.request, "__app_instrumented__", False):
        return

    original = requests_sessions.Session.request

    @functools.wraps(original)
    def wrapped(self, method, url, *args, **kwargs):
        scope = _scope.get()
        if scope is None:
            return original(self, method, url, *args, **kwargs)
        started = time.perf_counter()
        try:
            return original(self, method, url, *args, **kwargs)
        finally:
            scope.http_count += 1
            scope.http_ms += (time.perf_counter() - started) * 1000.0

    wrapped.__app_instrumented__ = True  # type: ignore[attr-defined]
    requests_sessions.Session.request = wrapped


_patch_requests()