from __future__ import annotations

import logging
import time
import uuid

from app.core.instrumentation import RequestScope, bind_scope, reset_scope
from app.core.metrics import metrics_store
from app.core.settings import settings


class TimingMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        access_logger = logging.getLogger("app.access")

        start = time.perf_counter()
        method = str(scope.get("method") or "")
        path = str(scope.get("path") or "")
        key = f"{method} {path}"
        query_string = scope.get("query_string") or b""
        query = query_string.decode("latin-1") if query_string else "-"
        headers = list(scope.get("headers") or [])

        rid: str | None = None
        xff: str | None = None
        for k, v in headers:
            lk = (k or b"").lower()
            if rid is None and lk == b"x-request-id":
                rid = (v or b"").decode("latin-1").strip()[:128]
            if xff is None and lk == b"x-forwarded-for":
                xff = (v or b"").decode("latin-1").strip()[:512]
            if rid is not None and xff is not None:
                break

        if not rid:
            rid = uuid.uuid4().hex[:12]

        client_ip = ""
        if xff:
            client_ip = xff.split(",", 1)[0].strip()
        if not client_ip:
            client = scope.get("client")
            if isinstance(client, (list, tuple)) and client:
                client_ip = str(client[0] or "")

        status_code = 0
        # 请求级诊断计数（SQL 条数/耗时、外部 HTTP 次数/耗时），只在本请求的
        # contextvar 存在时累加；同步端点会随着 anyio 线程池的上下文复制可见。
        diag = RequestScope()
        diag_token = bind_scope(diag)
        finished = False
        # 流式响应（SSE）的耗时等于整条流的生命周期，不能按普通慢请求告警。
        is_stream = False

        def finalize(elapsed_ms: float) -> None:
            nonlocal finished
            if finished:
                return
            finished = True
            metrics_store.incr("requests")
            metrics_store.incr("sql_statements", diag.sql_count)
            metrics_store.incr("external_http_calls", diag.http_count)
            slow_ms = int(getattr(settings, "slow_request_ms", 0) or 0)
            if is_stream:
                return
            if slow_ms > 0 and elapsed_ms >= slow_ms:
                metrics_store.incr("slow_requests")
                access_logger.warning(
                    "慢请求 rid=%s method=%s path=%s status=%d cost_ms=%.2f sql=%d sql_ms=%.2f http=%d http_ms=%.2f client_ip=%s",
                    rid,
                    method,
                    path,
                    status_code,
                    elapsed_ms,
                    diag.sql_count,
                    diag.sql_ms,
                    diag.http_count,
                    diag.http_ms,
                    client_ip,
                )

        async def send_wrapper(message):
            nonlocal status_code, is_stream
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status") or 0)
                ttfb_ms = (time.perf_counter() - start) * 1000.0
                out_headers = list(message.get("headers") or [])
                out_headers = [
                    (k, v)
                    for (k, v) in out_headers
                    if (k or b"").lower()
                    not in {b"x-response-time-ms", b"x-request-id", b"x-sql-count", b"x-sql-ms", b"x-http-count", b"x-http-ms"}
                ]
                out_headers.append((b"x-response-time-ms", f"{ttfb_ms:.2f}".encode("ascii")))
                out_headers.append((b"x-request-id", rid.encode("ascii", "ignore") or b"-"))
                # 诊断响应头：排障时不用翻日志就能看到这条请求打了多少次 SQL、等外部 HTTP 多久。
                out_headers.append((b"x-sql-count", str(diag.sql_count).encode("ascii")))
                out_headers.append((b"x-sql-ms", f"{diag.sql_ms:.2f}".encode("ascii")))
                out_headers.append((b"x-http-count", str(diag.http_count).encode("ascii")))
                out_headers.append((b"x-http-ms", f"{diag.http_ms:.2f}".encode("ascii")))
                message["headers"] = out_headers
                for (k, v) in out_headers:
                    if (k or b"").lower() == b"content-type" and b"text/event-stream" in (v or b""):
                        is_stream = True
            if message.get("type") == "http.response.body" and not message.get("more_body", False):
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                metrics_store.record(key, elapsed_ms)
                finalize(elapsed_ms)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            finalize(elapsed_ms)
            access_logger.exception(
                "rid=%s method=%s path=%s query=%s status=500 cost_ms=%.2f client_ip=%s sql=%d sql_ms=%.2f http=%d http_ms=%.2f",
                rid,
                method,
                path,
                query,
                elapsed_ms,
                client_ip,
                diag.sql_count,
                diag.sql_ms,
                diag.http_count,
                diag.http_ms,
            )
            raise
        finally:
            reset_scope(diag_token)
