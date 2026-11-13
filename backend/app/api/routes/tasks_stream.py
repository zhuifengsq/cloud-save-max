"""任务执行的 SSE 流式端点（从 app.api.routes.tasks 拆分，路径与事件契约不变）。

事件契约：event: init / log / stage / done，连接空闲 15 秒发一条 `: ping` 注释；
任务执行跑在独立线程（TaskExecutor 是同步阻塞栈），事件循环只负责消费队列写给客户端，
因此长时间运行的任务不再占用 anyio 线程池令牌。客户端断开时 finally 会置 stopped，
执行线程的后续事件直接丢弃，避免向已断开的连接堆积。
"""

from __future__ import annotations

from app.application.dto.task_run import RunTaskCommand
from app.application.container import run_task_use_case


import asyncio
import json
import logging
import threading
from collections import deque
from datetime import datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.application.use_cases.run_task import RunPolicy
from app.api.deps import CurrentUser, get_current_user_scoped, require_permissions_scoped
from app.core.permissions import TASK_RUN
from app.application.container import SessionLocal, commit, tasks_repo
from app.application.container import execution_log_cls as _acc_ExecutionLog; ExecutionLog = _acc_ExecutionLog()
from app.application.container import task_admission_service as _acc_task_admission; task_admission = _acc_task_admission()
from app.schemas.task import TaskCreateIn
from app.application import audit
from app.api.presenters.task import execution_out
from app.application.queries.task_views import execution_view
from app.application.tasks import get_task

router = APIRouter()
logger = logging.getLogger(__name__)


class _SseQueue:
    """Bound pending log events and schedule at most one cross-thread wakeup."""

    def __init__(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._lock = threading.Lock()
        self._ready = asyncio.Event()
        self._events: deque[tuple[str, object]] = deque(maxlen=256)
        self._terminal: deque[tuple[str, object]] = deque(maxlen=2)
        self._wake_scheduled = False
        self._stopped = False

    def _wake(self) -> None:
        with self._lock:
            self._wake_scheduled = False
            self._ready.set()

    def put(self, item: tuple[str, object]) -> None:
        with self._lock:
            if self._stopped:
                return
            if item[0] in {"done", "done_sentinel"}:
                self._terminal.append(item)
            else:
                self._events.append((item[0], str(item[1])[:4096]))
            if not self._wake_scheduled:
                self._wake_scheduled = True
                try:
                    self._loop.call_soon_threadsafe(self._wake)
                except RuntimeError:  # event loop shut down
                    self._stopped = True
                    self._events.clear()
                    self._terminal.clear()

    def stop(self) -> None:
        with self._lock:
            self._stopped = True
            self._events.clear()
            self._terminal.clear()

    async def get(self, timeout: float) -> tuple[str, object] | None:
        try:
            async with asyncio.timeout(timeout):
                while True:
                    with self._lock:
                        if self._events:
                            return self._events.popleft()
                        if self._terminal:
                            return self._terminal.popleft()
                        self._ready.clear()
                    await self._ready.wait()
        except TimeoutError:
            return None


def _sse_bytes(event: str, data: object) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n".encode("utf-8")


def _stream_response(q: _SseQueue, init_payload: dict) -> StreamingResponse:
    async def gen():
        try:
            yield _sse_bytes("init", init_payload)
            while True:
                item = await q.get(15)
                if item is None:
                    yield b": ping\n\n"
                    continue
                kind, value = item
                if kind == "log":
                    yield _sse_bytes("log", {"line": str(value)})
                    continue
                if kind == "stage":
                    yield _sse_bytes("stage", {"stage": str(value)})
                    continue
                if kind == "done":
                    yield _sse_bytes("done", value)
                    continue
                if kind == "done_sentinel":
                    break
        finally:
            # 客户端断开时尽快停止投递，避免执行线程继续向队列堆积无用事件。
            q.stop()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post('/{task_id:int}/run/stream', dependencies=[Depends(require_permissions_scoped(TASK_RUN))])
async def post_run_task_stream(request: Request, task_id: int, current: CurrentUser = Depends(get_current_user_scoped)):
    actor_id = int(current.user.id)
    request_ip = request.client.host if request.client else None
    request_agent = request.headers.get('user-agent')
    def _prepare() -> dict:
        with SessionLocal() as adb:
            task = get_task(adb, task_id)
            payload = {
                "task_id": int(task.id),
                "taskname": str(task.taskname),
                "started_at": datetime.now().isoformat(),
            }
            return payload

    slot = task_admission.reserve(task_id)
    try:
        init_payload = await run_in_threadpool(_prepare)
    except BaseException:
        slot.release()
        raise

    q = _SseQueue()

    def emit_line(line: str) -> None:
        q.put(("log", line))

    def emit_stage(stage: str) -> None:
        q.put(("stage", stage))

    def worker() -> None:
        with SessionLocal() as wdb:
            log = ExecutionLog(emit_line=emit_line, emit_stage=emit_stage)
            try:
                execution = run_task_use_case(wdb).execute(
                    RunTaskCommand(task_id, actor_user_id=actor_id,
                        ip=request_ip,
                        user_agent=request_agent),
                    RunPolicy.for_trigger('sse'), log=log)
                q.put(
                    (
                        "done",
                        {
                            "status": execution.status,
                            "message": execution.message,
                            "execution": execution_out(execution_view(execution)).model_dump(mode="json"),
                        },
                    )
                )
            except Exception as exc:
                wdb.rollback()
                message = getattr(exc, "message", None) or str(exc).strip() or type(exc).__name__
                log.section("异常")
                log.line(message)
                q.put(
                    (
                        "done",
                        {
                            "status": "failed",
                            "message": message,
                            "execution": {
                                "id": 0,
                                "task_id": task_id,
                                "status": "failed",
                                "started_at": log.started_at.isoformat(),
                                "finished_at": datetime.now().isoformat(),
                                "tree_summary": None,
                                "message": message,
                                "stage": log.stage,
                                "run_log": log.render(),
                                "adapter_snapshot": {},
                            },
                        },
                    )
                )
            finally:
                q.put(("done_sentinel", object()))

    slot.start_worker(worker)

    return _stream_response(q, init_payload)


@router.post('/run/stream', dependencies=[Depends(require_permissions_scoped(TASK_RUN))])
async def post_run_task_stream_by_payload(request: Request, payload: TaskCreateIn, current: CurrentUser = Depends(get_current_user_scoped)):
    actor_id = int(current.user.id)
    request_ip = request.client.host if request.client else None
    request_agent = request.headers.get('user-agent')
    import uuid

    def _prepare() -> dict:
        with SessionLocal() as adb:
            payload_out = {
                "task_id": 0,
                "taskname": str(payload.taskname),
                "started_at": datetime.now().isoformat(),
                "preview": True,
            }
            return payload_out

    slot = task_admission.reserve(None)
    try:
        init_payload = await run_in_threadpool(_prepare)
    except BaseException:
        slot.release()
        raise

    q = _SseQueue()

    def emit_line(line: str) -> None:
        q.put(("log", line))

    def emit_stage(stage: str) -> None:
        q.put(("stage", stage))

    def worker() -> None:
        with SessionLocal() as wdb:
            log = ExecutionLog(emit_line=emit_line, emit_stage=emit_stage)
            try:
                task_uid = (str(payload.task_uid or "").strip() or f"preview-{uuid.uuid4().hex}")
                preview = dict(
                    task_uid=task_uid,
                    task_type=str(payload.task_type or "generic"),
                    taskname=str(payload.taskname or ""),
                    shareurl=str(payload.shareurl or ""),
                    savepath=str(payload.savepath or ""),
                    pattern=(str(payload.pattern) if payload.pattern is not None else None),
                    replace=(str(payload.replace) if payload.replace is not None else None),
                    ignore_extension=bool(payload.ignore_extension),
                    account_name=(str(payload.account_name) if payload.account_name is not None else None),
                    tmdb_id=(int(payload.tmdb_id) if payload.tmdb_id is not None else None),
                    tmdb_media_type=(str(payload.tmdb_media_type) if payload.tmdb_media_type is not None else None),
                    enabled=True,
                    addition_json=json.dumps(payload.addition or {}, ensure_ascii=False),
                    extra_json=json.dumps(payload.extra or {}, ensure_ascii=False),
                )
                execution = run_task_use_case(wdb).execute(
                    RunTaskCommand(0, preview=preview, actor_user_id=actor_id, ip=request_ip, user_agent=request_agent),
                    RunPolicy.for_trigger('preview'), log=log)
                q.put(
                    (
                        "done",
                        {
                            "status": execution.status,
                            "message": execution.message,
                            "execution": execution_out(execution_view(execution)).model_dump(mode="json"),
                        },
                    )
                )
            except Exception as exc:
                wdb.rollback()
                message = getattr(exc, "message", None) or str(exc).strip() or type(exc).__name__
                log.section("异常")
                log.line(message)
                q.put(
                    (
                        "done",
                        {
                            "status": "failed",
                            "message": message,
                            "execution": {
                                "id": 0,
                                "task_id": 0,
                                "status": "failed",
                                "started_at": log.started_at.isoformat(),
                                "finished_at": datetime.now().isoformat(),
                                "tree_summary": None,
                                "message": message,
                                "stage": log.stage,
                                "run_log": log.render(),
                                "adapter_snapshot": {},
                            },
                        },
                    )
                )
            finally:
                q.put(("done_sentinel", object()))

    slot.start_worker(worker)

    return _stream_response(q, init_payload)
