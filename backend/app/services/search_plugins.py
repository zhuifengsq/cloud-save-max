from __future__ import annotations

import ast
import importlib.util
import inspect
import logging
import re
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import bad_request, not_found
from app.extensions.search_plugins.base import BaseSearchPlugin
from app.models.resource_search_source import ResourceSearchSource


logger = logging.getLogger(__name__)
PLUGIN_DIR = Path(__file__).resolve().parents[1] / "extensions" / "search_plugins"
MAX_PLUGIN_BYTES = 256 * 1024
MAX_RESULTS_PER_PLUGIN = 50
_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,24}$")
_FILENAME_PATTERN = re.compile(r"^([a-z][a-z0-9_]{0,24})_plugin\.py$")
_upload_lock = threading.RLock()
_active_searches = threading.BoundedSemaphore(8)


def _source_key(key: str) -> str:
    if not _KEY_PATTERN.fullmatch(key):
        raise bad_request("SEARCH_PLUGIN_KEY_INVALID", "插件标识只能使用小写英文字母、数字和下划线，且最长 25 字符")
    return f"plugin:{key}"


def _load_plugin(path: Path, *, expected_key: str) -> BaseSearchPlugin:
    module_name = f"app.extensions.search_plugins._loaded_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ValueError("无法读取 Python 插件")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    classes = [
        value for value in vars(module).values()
        if inspect.isclass(value)
        and issubclass(value, BaseSearchPlugin)
        and value is not BaseSearchPlugin
        and value.__module__ == module_name
    ]
    if len(classes) != 1:
        raise ValueError("文件必须定义一个继承 BaseSearchPlugin 的插件类")
    plugin = classes[0]()
    key = str(plugin.key or "").strip()
    if key != expected_key or not _KEY_PATTERN.fullmatch(key):
        raise ValueError("插件 key 必须与文件名一致")
    if not str(plugin.name or "").strip():
        raise ValueError("插件 name 不能为空")
    return plugin


def discover_search_plugins(*, plugin_dir: Path = PLUGIN_DIR) -> list[dict[str, Any]]:
    """Discover files without letting one invalid plugin hide the rest."""
    if not plugin_dir.is_dir():
        return []
    discovered: list[dict[str, Any]] = []
    for path in sorted(plugin_dir.glob("*_plugin.py")):
        if path.is_symlink() or not path.is_file():
            continue
        match = _FILENAME_PATTERN.fullmatch(path.name)
        if not match:
            continue
        key = match.group(1)
        try:
            plugin = _load_plugin(path, expected_key=key)
            discovered.append({
                "key": key,
                "name": str(plugin.name),
                "description": str(plugin.description or ""),
                "status": "ready",
                "error": None,
                "plugin": plugin,
            })
        except Exception as exc:
            logger.warning("搜索插件加载失败: %s: %s", path.name, exc)
            discovered.append({
                "key": key,
                "name": key,
                "description": "",
                "status": "error",
                "error": str(exc)[:300],
                "plugin": None,
            })
    return discovered


def _ensure_rows(db: Session, discovered: list[dict[str, Any]]) -> dict[str, ResourceSearchSource]:
    keys = [_source_key(item["key"]) for item in discovered]
    if not keys:
        return {}
    rows = {
        row.key: row
        for row in db.execute(select(ResourceSearchSource).where(ResourceSearchSource.key.in_(keys))).scalars().all()
    }
    for key in keys:
        if key not in rows:
            row = ResourceSearchSource(key=key, enabled=False, config_json="{}")
            db.add(row)
            rows[key] = row
    db.flush()
    return rows


def list_search_plugins(db: Session, *, plugin_dir: Path = PLUGIN_DIR) -> list[dict[str, Any]]:
    discovered = discover_search_plugins(plugin_dir=plugin_dir)
    rows = _ensure_rows(db, discovered)
    return [
        {
            "key": item["key"],
            "name": item["name"],
            "description": item["description"],
            "enabled": bool(rows[_source_key(item["key"])].enabled),
            "status": item["status"],
            "error": item["error"],
        }
        for item in discovered
    ]


def set_search_plugin_enabled(db: Session, key: str, enabled: bool, *, plugin_dir: Path = PLUGIN_DIR) -> dict[str, Any]:
    item = next((item for item in discover_search_plugins(plugin_dir=plugin_dir) if item["key"] == key), None)
    if item is None:
        raise not_found("SEARCH_PLUGIN_NOT_FOUND", "搜索插件不存在")
    if enabled and item["status"] != "ready":
        raise bad_request("SEARCH_PLUGIN_LOAD_FAILED", "插件加载失败，无法启用", detail=item["error"])
    rows = _ensure_rows(db, [item])
    rows[_source_key(key)].enabled = bool(enabled)
    db.flush()
    return next(entry for entry in list_search_plugins(db, plugin_dir=plugin_dir) if entry["key"] == key)


def normalize_search_results(plugin: BaseSearchPlugin, raw_results: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_results, list):
        raise ValueError("search(keyword) 必须返回列表")
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = str(item.get("shareurl") or item.get("url") or item.get("share_link") or "").strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc or url in seen:
            continue
        title = str(item.get("taskname") or item.get("title") or item.get("name") or "").strip()
        if not title:
            continue
        seen.add(url)
        results.append({
            "taskname": title[:512],
            "shareurl": url[:2048],
            "datetime": str(item.get("datetime") or "")[:64] or None,
            "content": str(item.get("content") or item.get("description") or "")[:2048] or None,
            "source": str(plugin.name),
        })
        if len(results) >= MAX_RESULTS_PER_PLUGIN:
            break
    return results


def _run_search(plugin: BaseSearchPlugin, keyword: str, *, timeout_seconds: float = 8.0) -> tuple[list[dict[str, Any]], str | None]:
    if not _active_searches.acquire(blocking=False):
        return [], "搜索插件并发已满"
    result: dict[str, Any] = {}
    finished = threading.Event()

    def worker() -> None:
        try:
            result["items"] = normalize_search_results(plugin, plugin.search(keyword))
        except Exception as exc:
            result["error"] = str(exc)[:300]
        finally:
            _active_searches.release()
            finished.set()

    threading.Thread(target=worker, daemon=True, name=f"search-plugin-{plugin.key}").start()
    if not finished.wait(timeout=max(1.0, min(float(timeout_seconds), 30.0))):
        return [], "搜索超时"
    return result.get("items", []), result.get("error")


def search_enabled_plugins(db: Session, keyword: str, *, plugin_dir: Path = PLUGIN_DIR) -> list[dict[str, Any]]:
    keyword = str(keyword or "").strip()
    if len(keyword) < 2:
        return []
    discovered = discover_search_plugins(plugin_dir=plugin_dir)
    rows = _ensure_rows(db, discovered)
    active = [item for item in discovered if item["status"] == "ready" and rows[_source_key(item["key"])].enabled]
    results: list[dict[str, Any]] = []
    if not active:
        return results
    with ThreadPoolExecutor(max_workers=min(len(active), 5)) as executor:
        futures = {
            executor.submit(_run_search, item["plugin"], keyword, timeout_seconds=item["plugin"].timeout_seconds): item["key"]
            for item in active
        }
        for future in as_completed(futures):
            matches, error = future.result()
            if error:
                logger.warning("搜索插件执行失败: %s: %s", futures[future], error)
            results.extend(matches)
    return results


def test_search_plugin(db: Session, key: str, keyword: str, *, plugin_dir: Path = PLUGIN_DIR) -> dict[str, Any]:
    item = next((item for item in discover_search_plugins(plugin_dir=plugin_dir) if item["key"] == key), None)
    if item is None:
        raise not_found("SEARCH_PLUGIN_NOT_FOUND", "搜索插件不存在")
    if item["status"] != "ready":
        return {"success": False, "message": item["error"], "latency_ms": 0, "items": []}
    started = time.monotonic()
    matches, error = _run_search(item["plugin"], str(keyword or "").strip(), timeout_seconds=item["plugin"].timeout_seconds)
    return {
        "success": error is None,
        "message": error or f"找到 {len(matches)} 条链接",
        "latency_ms": int((time.monotonic() - started) * 1000),
        "items": matches,
    }


def upload_search_plugin(filename: str, data: bytes, *, plugin_dir: Path = PLUGIN_DIR) -> dict[str, Any]:
    """Validate a candidate in a staging file, then create its final file exclusively."""
    filename = str(filename or "")
    match = _FILENAME_PATTERN.fullmatch(filename)
    if match is None or Path(filename).name != filename:
        raise bad_request("SEARCH_PLUGIN_FILENAME_INVALID", "文件名须为 <key>_plugin.py，key 只能含小写英文字母、数字和下划线")
    key = match.group(1)
    if not data or len(data) > MAX_PLUGIN_BYTES:
        raise bad_request("SEARCH_PLUGIN_SIZE_INVALID", "插件文件须为 1 至 256 KiB")
    try:
        source = data.decode("utf-8-sig")
        ast.parse(source, filename=filename)
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise bad_request("SEARCH_PLUGIN_SYNTAX_INVALID", "插件须为 UTF-8 编码且语法正确", detail=str(exc)[:300]) from exc
    plugin_dir.mkdir(parents=True, exist_ok=True)
    destination = plugin_dir / filename
    with _upload_lock:
        if destination.exists() or destination.is_symlink():
            raise bad_request("SEARCH_PLUGIN_EXISTS", "同名插件已存在；请先在目录中备份或移除旧文件")
        staging = plugin_dir / f".upload_{uuid.uuid4().hex}.py"
        try:
            with staging.open("xb") as handle:
                handle.write(data)
            try:
                plugin = _load_plugin(staging, expected_key=key)
            except Exception as exc:
                raise bad_request("SEARCH_PLUGIN_LOAD_FAILED", "插件定义校验失败", detail=str(exc)[:300]) from exc
            with destination.open("xb") as handle:
                handle.write(data)
            return {"key": key, "name": str(plugin.name), "description": str(plugin.description or "")}
        finally:
            staging.unlink(missing_ok=True)
