from __future__ import annotations

import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import quote, urlparse

import jmespath
import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import bad_request, not_found
from app.models.resource_search_source import ResourceSearchSource


API_KEY_PREFIX = "api:"
MAX_RESULTS_PER_SOURCE = 100
DEFAULT_PATHS = {
    "items_path": "data.items",
    "title_path": "title",
    "url_path": "url",
    "password_path": "password",
    "datetime_path": "datetime",
}


def _config(row: ResourceSearchSource) -> dict[str, Any]:
    try:
        value = json.loads(row.config_json or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _validate_url(value: str) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise bad_request("SEARCH_API_URL_INVALID", "API 地址必须是 http 或 https URL")
    return url


def _clean_config(payload: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    data = dict(previous or {})
    for key in ("name", "method", "url", "response_rule", "items_path", "title_path", "url_path", "password_path", "datetime_path"):
        if key in payload:
            data[key] = str(payload[key] or "").strip()
    for key in ("headers", "query_params", "body"):
        if key in payload:
            value = payload[key]
            if not isinstance(value, dict):
                raise bad_request("SEARCH_API_CONFIG_INVALID", f"{key} 必须是 JSON 对象")
            data[key] = value
    data["name"] = str(data.get("name") or "").strip()
    if not data["name"]:
        raise bad_request("SEARCH_API_NAME_REQUIRED", "请输入搜索源名称")
    data["url"] = _validate_url(str(data.get("url") or ""))
    data["method"] = str(data.get("method") or "GET").upper()
    if data["method"] not in {"GET", "POST"}:
        raise bad_request("SEARCH_API_METHOD_INVALID", "仅支持 GET 或 POST")
    if data.get("response_rule"):
        try:
            jmespath.compile(data["response_rule"])
        except jmespath.exceptions.JMESPathError as exc:
            raise bad_request("SEARCH_API_RESPONSE_RULE_INVALID", f"JMESPath 解析规则无效：{exc}") from exc
    for key, default in DEFAULT_PATHS.items():
        data[key] = str(data.get(key) or default).strip()
    for key in ("headers", "query_params", "body"):
        data.setdefault(key, {})
    return data


def _out(row: ResourceSearchSource) -> dict[str, Any]:
    return {"id": row.id, "enabled": bool(row.enabled), **_config(row)}


def list_api_sources(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        select(ResourceSearchSource)
        .where(ResourceSearchSource.key.like(f"{API_KEY_PREFIX}%"))
        .order_by(ResourceSearchSource.id.desc())
    ).scalars().all()
    return [_out(row) for row in rows]


def get_api_source(db: Session, source_id: int) -> ResourceSearchSource:
    row = db.get(ResourceSearchSource, int(source_id))
    if row is None or not row.key.startswith(API_KEY_PREFIX):
        raise not_found("SEARCH_API_SOURCE_NOT_FOUND", "搜索源不存在")
    return row


def create_api_source(db: Session, payload: dict[str, Any]) -> dict[str, Any]:
    config = _clean_config(payload)
    row = ResourceSearchSource(
        key=f"{API_KEY_PREFIX}{uuid.uuid4().hex[:16]}",
        enabled=bool(payload.get("enabled", True)),
        config_json=json.dumps(config, ensure_ascii=False),
    )
    db.add(row)
    db.flush()
    return _out(row)


def update_api_source(db: Session, source_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    row = get_api_source(db, source_id)
    row.config_json = json.dumps(_clean_config(payload, _config(row)), ensure_ascii=False)
    if "enabled" in payload:
        row.enabled = bool(payload["enabled"])
    db.flush()
    return _out(row)


def delete_api_source(db: Session, source_id: int) -> None:
    db.delete(get_api_source(db, source_id))
    db.flush()


def _extract(value: Any, path: str) -> Any:
    """Read a simple dotted path, including numeric list indexes and `[]` flattening."""
    if not path or path == "$":
        return value
    values = [value]
    for token in re.split(r"\.(?![^\[]*\])", path):
        flatten = token.endswith("[]")
        token = token[:-2] if flatten else token
        next_values: list[Any] = []
        for item in values:
            if token.isdigit() and isinstance(item, list):
                index = int(token)
                result = item[index] if index < len(item) else None
            elif token and isinstance(item, dict):
                result = item.get(token)
            else:
                result = item if not token else None
            if flatten and isinstance(result, list):
                next_values.extend(result)
            elif result is not None:
                next_values.append(result)
        values = next_values
    return values if path.endswith("[]") else (values[0] if values else None)


def _fill(value: Any, keyword: str) -> Any:
    if isinstance(value, str):
        return value.replace("[[keyword]]", keyword).replace("{keyword}", keyword)
    if isinstance(value, dict):
        return {key: _fill(item, keyword) for key, item in value.items()}
    if isinstance(value, list):
        return [_fill(item, keyword) for item in value]
    return value


def _result_row(item: Any, keyword: str, config: dict[str, Any]) -> dict[str, Any] | None:
    if isinstance(item, (list, tuple)) and len(item) >= 2:
        title, shareurl = item[:2]
        password = item[2] if len(item) > 2 else None
        datetime = item[3] if len(item) > 3 else None
    elif isinstance(item, dict):
        if config.get("response_rule"):
            title = next((item.get(key) for key in ("taskname", "title", "name", "file_name") if item.get(key)), None)
            shareurl = next((item.get(key) for key in ("shareurl", "url", "share_link", "link", "data_url") if item.get(key)), None)
            password = next((item.get(key) for key in ("password", "pwd", "code") if item.get(key)), None)
            datetime = next((item.get(key) for key in ("datetime", "created_at", "time", "pub_date") if item.get(key)), None)
        else:
            title = _extract(item, str(config.get("title_path") or "title"))
            shareurl = _extract(item, str(config.get("url_path") or "url"))
            password = _extract(item, str(config.get("password_path") or "password"))
            datetime = _extract(item, str(config.get("datetime_path") or "datetime"))
    else:
        return None
    url = str(shareurl or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or len(url) > 2048:
        return None
    source = str(config.get("name") or "API 搜索源")
    return {
        "taskname": (str(title or "").strip() or keyword)[:512],
        "shareurl": url,
        "password": str(password or "").strip() or None,
        "datetime": str(datetime or "").strip()[:64] or None,
        "source": source,
        "channel": source,
    }


def search_api_source(config: dict[str, Any], keyword: str) -> list[dict[str, Any]]:
    url = _validate_url(str(config.get("url") or ""))
    method = str(config.get("method") or "GET").upper()
    request_body = _fill(config.get("body") or {}, keyword)
    query_params = _fill(config.get("query_params") or {}, keyword)
    kwargs: dict[str, Any] = {
        "headers": _fill(config.get("headers") or {}, keyword),
        "params": {**request_body, **query_params} if method == "GET" else query_params,
        "timeout": (5, 12),
    }
    if method == "POST":
        kwargs["json"] = request_body
    encoded_keyword = quote(str(keyword), safe="")
    request_url = url.replace("[[keyword]]", encoded_keyword).replace("{keyword}", encoded_keyword)
    response = requests.request(method, request_url, **kwargs)
    response.raise_for_status()
    body = response.json()
    rule = str(config.get("response_rule") or "").strip()
    items = jmespath.search(rule, body) if rule else _extract(body, str(config.get("items_path") or ""))
    if not isinstance(items, list):
        return []
    results: list[dict[str, Any]] = []
    for item in items[:MAX_RESULTS_PER_SOURCE]:
        row = _result_row(item, keyword, config)
        if row is not None:
            results.append(row)
    return results


def search_enabled_api_sources(db: Session, keyword: str) -> list[dict[str, Any]]:
    rows = db.execute(
        select(ResourceSearchSource).where(
            ResourceSearchSource.key.like(f"{API_KEY_PREFIX}%"),
            ResourceSearchSource.enabled.is_(True),
        )
    ).scalars().all()
    configs = [_config(row) for row in rows]
    results: list[dict[str, Any]] = []
    if not configs:
        return results
    with ThreadPoolExecutor(max_workers=min(len(configs), 5)) as executor:
        futures = [executor.submit(search_api_source, config, keyword) for config in configs]
        for future in as_completed(futures):
            try:
                results.extend(future.result())
            except Exception:
                continue
    return results
