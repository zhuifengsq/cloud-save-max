# -*- coding: utf-8 -*-
"""前后端契约 golden（S5 非阻塞小项②）：响应模型 / SSE 线格式 / 错误体三处「不能悄悄变」。

为什么需要它：重构期最容易发生的破坏不是报错，而是**字段改名、字段消失、SSE 少一个事件、
错误体多一层包裹**——这类变化后端测试全绿、前端静默显示空白。这一层把契约变成可 diff 的文件：

1. 后端模型契约：每个响应模型（含顺序）落盘到 golden/frontend_contract.json；
2. 前端类型契约：frontend/src/types/tasks.ts 的类型字段集合也落盘，任何一侧改了字段都必须
   同步改 golden，并且「后端有、前端没有」的字段必须在 DRIFT_ALLOWLIST 里登记理由；
3. SSE 线格式：逐字节锁定 event: init/log/stage/done 的文本与 JSON 序列化方式（ensure_ascii=False）；
4. 错误体：{code, message, detail} 与 500 时只有 {code, message}。

更新方式（只在确认契约变更时用，并把 diff 一起提交）：
    UPDATE_CONTRACT_GOLDEN=1 python -m pytest regression_tests/test_frontend_contract_golden.py -q

对应方案 §4.3 / §10.19。
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import os
import re
from pathlib import Path

GOLDEN_PATH = Path(__file__).resolve().parent / "golden" / "frontend_contract.json"
REPO_ROOT = Path(__file__).resolve().parents[2]
TS_TASKS = REPO_ROOT / "frontend" / "src" / "types" / "tasks.ts"
TS_MEDIA = REPO_ROOT / "frontend" / "src" / "types" / "media.ts"

# (TS 类型名, 后端模块, 后端模型名)
MODEL_PAIRS = [
    ("TaskItem", "app.schemas.task", "TaskOut"),
    ("TaskExecutionItem", "app.schemas.task", "TaskExecutionOut"),
    ("TaskShareLink", "app.schemas.task", "TaskShareLinkOut"),
    ("TaskSchedulerSetting", "app.schemas.task_scheduler", "TaskSchedulerSettingOut"),
    ("SharePreviewItem", "app.application.dto.task_browse", "SharePreviewItemOut"),
    ("SharePreviewResponse", "app.application.dto.task_browse", "SharePreviewOut"),
    ("SharePreviewBatchItem", "app.application.dto.task_browse", "SharePreviewBatchItemOut"),
    ("SharePreviewBatchResponse", "app.application.dto.task_browse", "SharePreviewBatchOut"),
    ("DriveBrowseItem", "app.application.dto.task_browse", "DriveBrowseItemOut"),
    ("DriveBrowseResponse", "app.application.dto.task_browse", "DriveBrowseOut"),
    ("TaskExecutionSummary", "app.schemas.task_reads", "TaskExecutionSummaryOut"),
    ("TaskSummary", "app.schemas.task_reads", "TaskSummaryOut"),
    ("TaskOverview", "app.schemas.task_reads", "TaskOverviewOut"),
    ("TaskDetail", "app.schemas.task_reads", "TaskSummaryOut"),
    ("TaskFacets", "app.schemas.task_reads", "TaskFacetsOut"),
    ("TaskPage", "app.schemas.task_reads", "TaskPageOut"),
    ("TaskOrderIndexItem", "app.schemas.task_reads", "TaskOrderIndexItemOut"),
    ("TaskExecutionPage", "app.schemas.task_reads", "TaskExecutionPageOut"),
    ("TaskExecutionDetail", "app.schemas.task_reads", "TaskExecutionDetailOut"),
    ("TMDBImage", "app.schemas.media_discovery", "TMDBImageOut"),
    ("TMDBImages", "app.schemas.media_discovery", "TMDBImagesOut"),
    ("SavedLatestProgress", "app.schemas.saved_progress", "SavedLatestProgressOut"),
    ("SavedProgressCorrection", "app.schemas.saved_progress", "SavedProgressCorrectionOut"),
]

# 「后端有、前端没有」的已确认差异：字段 -> 理由。空理由视为未登记。
DRIFT_ALLOWLIST: dict[str, dict[str, dict[str, str]]] = {
    "SharePreviewItem": {
        "backend_only": {
            "filtered_by_file_filter": "后端过筛标记，前端预览列表不渲染",
            "filtered_by_file_date": "后端过筛标记，前端预览列表不渲染",
            "filtered_by_folder": "后端过筛标记，前端预览列表不渲染",
            "filtered_by_search": "后端过筛标记，前端预览列表不渲染",
            "priority_match": "后端目录优先级标记，前端按 name_re 自行着色",
        },
    },
    "SharePreviewBatchItem": {
        "backend_only": {
            "share_author_name": "批量预览前端不展示分享者名，单条预览才展示",
        },
    },
}

SSE_INIT = {"task_id": 7, "taskname": "测试任务", "started_at": "2026-11-09T00:00:00"}
SSE_ITEMS = [
    ("log", "开始执行"),
    ("stage", "transfer"),
    ("done", {"status": "success", "execution_id": 11}),
    ("done_sentinel", None),
]


def _model_fields(module_name: str, model_name: str) -> dict[str, str]:
    module = importlib.import_module(module_name)
    model = getattr(module, model_name)
    return {name: str(field.annotation) for name, field in model.model_fields.items()}


def _backend_models() -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for _ts_name, module_name, model_name in MODEL_PAIRS:
        out[model_name] = _model_fields(module_name, model_name)
    return out


def _ts_types(path: Path = TS_TASKS) -> dict[str, list[str]]:
    """Resolve the object/Pick/Omit/intersection contracts used in tasks.ts.

    Fields may be separated by semicolons or newlines. Nested object members
    are deliberately excluded; aliases must remain visible to contract checks.
    """
    text = path.read_text(encoding="utf-8")
    starts = list(re.finditer(r"export type (\w+)\s*=\s*", text))
    expressions = {m.group(1): text[m.end(): starts[i + 1].start() if i + 1 < len(starts) else len(text)]
                   for i, m in enumerate(starts)}
    resolved: dict[str, list[str]] = {}

    def object_fields(expression: str) -> set[str]:
        fields: set[str] = set()
        depth = 0
        boundary = 0
        for i, char in enumerate(expression):
            if char == "{":
                depth += 1
                if depth == 1:
                    boundary = i + 1
            elif char == "}":
                if depth == 1:
                    part = expression[boundary:i].strip()
                    match = re.match(r"^(\w+)\??\s*:", part)
                    if match:
                        fields.add(match.group(1))
                depth -= 1
            elif depth == 1 and char in ";\n":
                part = expression[boundary:i].strip()
                match = re.match(r"^(\w+)\??\s*:", part)
                if match:
                    fields.add(match.group(1))
                boundary = i + 1
        return fields

    def resolve(name: str, visiting: frozenset[str] = frozenset()) -> list[str]:
        if name in resolved:
            return resolved[name]
        if name in visiting:
            raise AssertionError(f"cyclic frontend contract: {name}")
        expression = expressions[name]
        fields = object_fields(expression)
        utility = re.match(r"(Omit|Pick)<(\w+),\s*([^>]+)>", expression)
        if utility:
            operation, base, keys = utility.groups()
            base_fields = set(resolve(base, visiting | {name}))
            chosen = set(re.findall(r"['\"](\w+)['\"]", keys))
            fields |= base_fields - chosen if operation == "Omit" else base_fields & chosen
        else:
            base = re.match(r"(\w+)\s*&", expression)
            if base and base.group(1) in expressions:
                fields |= set(resolve(base.group(1), visiting | {name}))
        resolved[name] = sorted(fields)
        return resolved[name]

    for name in expressions:
        resolve(name)
    if path == TS_TASKS:
        media_types = _ts_types(TS_MEDIA)
        for name in ("TMDBImage", "TMDBImages"):
            resolved[name] = media_types[name]
    return resolved


def _sse_chunks(init_payload: dict, items: list[tuple[str, object]]) -> list[str]:
    from app.api.routes import tasks_stream

    class _FakeQueue:
        def __init__(self, queued):
            self._items = list(queued)

        async def get(self, _timeout):
            return self._items.pop(0) if self._items else None

        def stop(self) -> None:
            pass

    response = tasks_stream._stream_response(_FakeQueue(items), init_payload)

    async def collect() -> list[str]:
        chunks: list[str] = []
        async for chunk in response.body_iterator:
            chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk))
        return chunks

    return asyncio.run(collect())


# 校验错误的 detail 是 str(exc)，随 pydantic 版本变化，只锁「键顺序 + code + message」；
# 业务错误（ApiError）的 detail 是后端自己写的，锁整包。
def _sse_events(chunks: list[str]) -> list[str]:
    """从真实发出的字节里抽事件名，而不是从代码里的常量猜测。"""
    names: set[str] = set()
    for chunk in chunks:
        names.update(re.findall(r"^event: (\w+)", chunk, re.MULTILINE))
    return sorted(names)


def _error_bodies() -> dict[str, dict[str, object]]:
    from fastapi.exceptions import RequestValidationError

    from app.core import exception_handlers as handlers
    from app.core.errors import ApiError

    def _body(response) -> dict[str, object]:
        return json.loads(response.body.decode("utf-8"))

    api_response = handlers.api_error_handler(
        None, ApiError(code="TASK_NOT_FOUND", message="任务不存在", http_status=404, detail="id=7")
    )
    validation_response = handlers.validation_error_handler(
        None, RequestValidationError([{"type": "missing", "loc": ("body", "taskname"), "msg": "Field required"}])
    )
    internal_response = handlers.http_error_handler(None, RuntimeError("boom"))

    api_body = _body(api_response)
    validation_body = _body(validation_response)
    internal_body = _body(internal_response)
    return {
        "ApiError": {
            "status": api_response.status_code,
            "keys": list(api_body.keys()),
            "code": api_body["code"],
            "message": api_body["message"],
            "detail": api_body["detail"],
        },
        "RequestValidationError": {
            "status": validation_response.status_code,
            "keys": list(validation_body.keys()),
            "code": validation_body["code"],
            "message": validation_body["message"],
        },
        "UnhandledException": {
            "status": internal_response.status_code,
            "keys": list(internal_body.keys()),
            "code": internal_body["code"],
            "message": internal_body["message"],
        },
    }


def _validate_stream_sentinel() -> str:
    from app.api.routes import tasks_share

    source = inspect.getsource(tasks_share)
    match = re.search(r'yield "(data: \[DONE\]\\n\\n)"', source)
    assert match, "tasks_share 里找不到 data: [DONE] 哨兵行"
    # 源码里是转义写法 "\n"，还原成运行时真正发出的两个换行
    return match.group(1).replace("\\n", "\n")


def _build_snapshot() -> dict[str, object]:
    from app.api.routes import tasks_stream

    return {
        "_comment": "由 regression_tests/test_frontend_contract_golden.py 生成；改动请连同 diff 一起提交",
        "backend_models": _backend_models(),
        "frontend_types": _ts_types(),
        "sse": {
            "run_stream": {
                "media_type": "text/event-stream",
                "headers": {
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
                "events": _sse_events(_sse_chunks(SSE_INIT, SSE_ITEMS)),
                "chunks": _sse_chunks(SSE_INIT, SSE_ITEMS),
                "ping_chunks": _sse_chunks(SSE_INIT, [None, ("done_sentinel", None)]),
                "sse_bytes": tasks_stream._sse_bytes("stage", {"stage": "transfer"}).decode("utf-8"),
            },
            "validate_stream": {"done_sentinel": _validate_stream_sentinel()},
        },
        "error_bodies": _error_bodies(),
    }


def _load_golden() -> dict[str, object]:
    snapshot = _build_snapshot()
    if os.environ.get("UPDATE_CONTRACT_GOLDEN") == "1":
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    assert GOLDEN_PATH.exists(), (
        "缺少 golden 文件，先运行 UPDATE_CONTRACT_GOLDEN=1 python -m pytest "
        "regression_tests/test_frontend_contract_golden.py -q"
    )
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def test_backend_models_match_golden():
    golden = _load_golden()
    assert _backend_models() == golden["backend_models"]


def test_frontend_types_match_golden():
    golden = _load_golden()
    assert _ts_types() == golden["frontend_types"], "frontend/src/types/tasks.ts 的类型字段变了，先确认是否同步后端"


def test_frontend_and_backend_field_sets_are_aligned():
    backend = {model_name: set(fields) for model_name, fields in _backend_models().items()}
    frontend = _ts_types()
    problems: list[str] = []

    for ts_name, _module_name, model_name in MODEL_PAIRS:
        if ts_name not in frontend:
            problems.append(f"{ts_name}: 前端类型文件里找不到该类型")
            continue
        allowed = DRIFT_ALLOWLIST.get(ts_name, {})
        allowed_backend_only = set(allowed.get("backend_only", {}))
        allowed_frontend_only = set(allowed.get("frontend_only", {}))
        for field, reason in {**allowed.get("backend_only", {}), **allowed.get("frontend_only", {})}.items():
            if not reason.strip():
                problems.append(f"{ts_name}.{field}: 白名单缺少理由")
        backend_only = backend[model_name] - set(frontend[ts_name])
        frontend_only = set(frontend[ts_name]) - backend[model_name]
        if backend_only != allowed_backend_only:
            problems.append(
                f"{ts_name} ↔ {model_name}: 后端多出未登记字段 {sorted(backend_only - allowed_backend_only)}，"
                f"白名单里已失效的 {sorted(allowed_backend_only - backend_only)}"
            )
        if frontend_only != allowed_frontend_only:
            problems.append(
                f"{ts_name} ↔ {model_name}: 前端多出未登记字段 {sorted(frontend_only - allowed_frontend_only)}，"
                f"白名单里已失效的 {sorted(allowed_frontend_only - frontend_only)}"
            )

    stale = set(DRIFT_ALLOWLIST) - {ts_name for ts_name, _m, _n in MODEL_PAIRS}
    if stale:
        problems.append(f"DRIFT_ALLOWLIST 里有已不存在的类型 {sorted(stale)}")

    assert not problems, "\n".join(problems)


def test_sse_wire_format_matches_golden():
    golden = _load_golden()
    run_stream = golden["sse"]["run_stream"]
    assert run_stream["chunks"] == _sse_chunks(SSE_INIT, SSE_ITEMS)
    assert run_stream["ping_chunks"] == _sse_chunks(SSE_INIT, [None, ("done_sentinel", None)])
    assert run_stream["media_type"] == "text/event-stream"
    assert run_stream["headers"] == {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def test_validate_stream_sentinel_matches_golden():
    golden = _load_golden()
    assert golden["sse"]["validate_stream"]["done_sentinel"] == _validate_stream_sentinel()


def test_error_body_matches_golden():
    golden = _load_golden()
    bodies = _error_bodies()
    assert bodies == golden["error_bodies"]
    assert bodies["ApiError"]["keys"] == ["code", "message", "detail"]
    assert bodies["RequestValidationError"]["keys"] == ["code", "message", "detail"]
    assert bodies["UnhandledException"]["keys"] == ["code", "message"]
