from __future__ import annotations

from dataclasses import dataclass
import hashlib
import ipaddress
import json
import os
import re
import socket
import threading
import time
import uuid
from urllib.parse import SplitResult, urlsplit

from fastapi import Response
from fastapi.responses import FileResponse
from urllib3 import PoolManager, Timeout
from urllib3.exceptions import HTTPError
from urllib3.exceptions import TimeoutError as UpstreamTimeout


@dataclass(frozen=True)
class ProxyImageCacheConfig:
    enabled: bool
    cache_dir: str
    ttl_seconds: int
    max_file_bytes: int
    max_total_bytes: int


_locks = tuple(threading.Lock() for _ in range(64))
_image_types = {
    "image/avif", "image/bmp", "image/gif", "image/jpeg", "image/png",
    "image/webp", "image/x-icon", "image/vnd.microsoft.icon",
}
_default_max_image_bytes = 10 * 1024 * 1024
_ipv4_translation = ipaddress.IPv6Network("64:ff9b::/96")
_image_pools = PoolManager(num_pools=32, maxsize=8)
_public_poster_path = re.compile(r"/view/photo/[sm]_ratio_poster/public/p[0-9]+\.(?:jpg|jpeg|png|webp|avif|gif)")


class ProxyImageError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code


def validate_image_url(url: str) -> SplitResult:
    try:
        if any(ord(char) <= 32 or ord(char) == 127 or char == "\\" for char in url):
            raise ValueError
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = 443 if parsed.scheme == "https" else 80
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, port}
            or parsed.fragment
            or not any(host == base or host.endswith("." + base) for base in ("douban.com", "doubanio.com"))
            or any(not label or not label.isascii() or not label.replace("-", "").isalnum() for label in host.split("."))
        ):
            raise ValueError
        return parsed
    except ValueError as exc:
        raise ProxyImageError(400) from exc


def _resolve_image_ip(host: str, port: int) -> str:
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise ProxyImageError(502)
    for family, _, _, _, endpoint in addresses:
        ip = ipaddress.ip_address(endpoint[0])
        checked = getattr(ip, "ipv4_mapped", None) or ip
        if (
            not checked.is_global or checked.is_multicast or checked.is_reserved
            or (isinstance(checked, ipaddress.IPv6Address) and (
                checked.is_site_local or checked.sixtofour is not None
                or checked.teredo is not None or checked in _ipv4_translation
            ))
            or (family == socket.AF_INET6 and (len(endpoint) != 4 or endpoint[3]))
        ):
            raise ProxyImageError(400)
    return next((entry[4][0] for entry in addresses if entry[0] == socket.AF_INET), addresses[0][4][0])


def _download_image(url: str, headers: dict[str, str], max_bytes: int) -> tuple[bytes, str]:
    parsed = validate_image_url(url)
    host = parsed.hostname
    scheme = parsed.scheme
    # img9 can return an HTML challenge for public posters shared by the image nodes.
    if host == "img9.doubanio.com" and _public_poster_path.fullmatch(parsed.path):
        host = "img2.doubanio.com"
        scheme = "https"
    deadline = time.monotonic() + 15
    port = 443 if scheme == "https" else 80
    try:
        address = _resolve_image_ip(host, port)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProxyImageError(504)
        # Connect to the validated numeric address while retaining the original HTTP Host
        # and TLS identity. A second DNS answer cannot redirect the connection.
        tls = {"server_hostname": host, "assert_hostname": host, "cert_reqs": "CERT_REQUIRED"} if scheme == "https" else None
        pool = _image_pools.connection_from_host(address, port, scheme=scheme, pool_kwargs=tls)
        path = parsed.path or "/"
        if parsed.query:
            path += "?" + parsed.query
        response = pool.urlopen(
            "GET", path, headers={**headers, "Host": host, "Accept-Encoding": "identity"},
            timeout=Timeout(total=remaining, connect=4, read=4),
            redirect=False, retries=False, preload_content=False,
        )
        try:
            if response.status != 200:
                raise ProxyImageError(response.status if 400 <= response.status <= 599 else 502)
            content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            encoding = str(response.headers.get("Content-Encoding") or "identity").strip().lower()
            if not _is_image_content_type(content_type) or encoding != "identity":
                raise ProxyImageError(502)
            length = _safe_int(response.headers.get("Content-Length"))
            if length is not None and length > max_bytes:
                raise ProxyImageError(413)
            body = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise ProxyImageError(504)
                chunk = response.read1(min(65536, max_bytes + 1 - len(body)), decode_content=False)
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ProxyImageError(413)
            if not body:
                raise ProxyImageError(502)
            return bytes(body), content_type
        finally:
            response.close()
            response.release_conn()
    except UpstreamTimeout as exc:
        raise ProxyImageError(504) from exc
    except (HTTPError, OSError) as exc:
        raise ProxyImageError(502) from exc


def _now_ts() -> int:
    return int(time.time())


def _sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_image_content_type(content_type: str | None) -> bool:
    return bool(content_type and content_type.split(";", 1)[0].strip().lower() in _image_types)


def resolve_proxy_image_cache_dir(*, database_url: str, explicit_dir: str | None) -> str:
    if explicit_dir and str(explicit_dir).strip():
        return str(explicit_dir).strip()
    if database_url.startswith("sqlite") and "///" in database_url:
        path = database_url.split("///", 1)[1]
        directory = os.path.dirname(path) or "./data"
        return os.path.join(directory, "cache", "proxy_image")
    return os.path.join("./data", "cache", "proxy_image")


def ensure_dir(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except OSError:
        return False


def _paths_for_url(cache_dir: str, url: str) -> tuple[str, str, str, str]:
    key = _sha256_hex(url)
    shard = key[:2]
    shard_dir = os.path.join(cache_dir, shard)
    meta_path = os.path.join(shard_dir, f"{key}.json")
    bin_path = os.path.join(shard_dir, f"{key}.bin")
    return key, shard_dir, meta_path, bin_path


def _read_meta(meta_path: str) -> dict | None:
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return None
    except (OSError, json.JSONDecodeError):
        return None


def _is_fresh(meta: dict) -> bool:
    expires_at = meta.get("expires_at")
    if not isinstance(expires_at, int):
        return False
    return expires_at > _now_ts()


def _format_etag(etag_hex: str) -> str:
    return f"\"{etag_hex}\""


def _if_none_match_hit(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    raw = str(if_none_match).strip()
    if not raw:
        return False
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if "*" in parts:
        return True
    if etag in parts:
        return True
    etag_unquoted = etag.strip("\"")
    return any(p.strip("\"") == etag_unquoted for p in parts)


def try_build_cached_response(
    *,
    cfg: ProxyImageCacheConfig,
    url: str,
    if_none_match: str | None,
    cache_control: str,
) -> tuple[Response | None, str]:
    if not cfg.enabled:
        return None, "BYPASS"

    key, shard_dir, meta_path, bin_path = _paths_for_url(cfg.cache_dir, url)
    if not os.path.exists(meta_path) or not os.path.exists(bin_path):
        return None, "MISS"

    meta = _read_meta(meta_path)
    if not meta or meta.get("validated_origin") != 1 or not _is_fresh(meta):
        _best_effort_delete(meta_path, bin_path)
        return None, "MISS"

    content_type = str(meta.get("content_type") or "image/jpeg")
    if not _is_image_content_type(content_type):
        _best_effort_delete(meta_path, bin_path)
        return None, "MISS"
    etag_hex = str(meta.get("etag") or key)
    etag = _format_etag(etag_hex)

    headers = {
        "Cache-Control": cache_control,
        "ETag": etag,
        "X-Content-Type-Options": "nosniff",
    }

    if _if_none_match_hit(if_none_match, etag):
        headers["X-Proxy-Cache"] = "HIT-304"
        return Response(status_code=304, headers=headers), "HIT-304"

    headers["X-Proxy-Cache"] = "HIT"
    return FileResponse(path=bin_path, media_type=content_type, headers=headers), "HIT"


def fetch_store_and_build_response(
    *,
    cfg: ProxyImageCacheConfig,
    url: str,
    cache_control: str,
    headers: dict[str, str],
) -> tuple[Response, str]:
    max_bytes = cfg.max_file_bytes if cfg.max_file_bytes > 0 else _default_max_image_bytes
    response_headers = {"Cache-Control": cache_control, "X-Content-Type-Options": "nosniff"}
    key, shard_dir, meta_path, bin_path = _paths_for_url(cfg.cache_dir, url)
    if not cfg.enabled or not ensure_dir(shard_dir):
        body, content_type = _download_image(url, headers, max_bytes)
        return Response(body, media_type=content_type, headers={**response_headers, "X-Proxy-Cache": "BYPASS"}), "BYPASS"

    with _locks[int(key[:2], 16) % len(_locks)]:
        cached, _ = try_build_cached_response(cfg=cfg, url=url, if_none_match=None, cache_control=cache_control)
        if cached is not None:
            return cached, "HIT"

        body, content_type = _download_image(url, headers, max_bytes)
        etag_hex = hashlib.sha256(body).hexdigest()
        tmp_path = os.path.join(shard_dir, f"{key}.{uuid.uuid4().hex}.tmp")
        try:
            with open(tmp_path, "wb") as f:
                f.write(body)
            os.replace(tmp_path, bin_path)
            _atomic_write_json(meta_path, {
                "url": url,
                "content_type": content_type,
                "etag": etag_hex,
                "size": len(body),
                "created_at": _now_ts(),
                "expires_at": _now_ts() + max(int(cfg.ttl_seconds), 1),
                "validated_origin": 1,
            })
            _enforce_total_size_limit(cfg=cfg)
        except OSError:
            return Response(body, media_type=content_type, headers={**response_headers, "X-Proxy-Cache": "BYPASS"}), "BYPASS"
        finally:
            _best_effort_delete(tmp_path, tmp_path)
        response_headers.update({"ETag": _format_etag(etag_hex), "X-Proxy-Cache": "MISS"})
        return Response(body, media_type=content_type, headers=response_headers), "MISS"


def _atomic_write_json(path: str, data: dict) -> None:
    directory = os.path.dirname(path)
    if directory:
        ensure_dir(directory)
    tmp_path = f"{path}.{uuid.uuid4().hex}.tmp"
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(payload)
    os.replace(tmp_path, path)


def _best_effort_delete(path_a: str, path_b: str) -> None:
    for p in {path_a, path_b}:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except OSError:
            continue


def _enforce_total_size_limit(*, cfg: ProxyImageCacheConfig) -> None:
    max_total = int(cfg.max_total_bytes or 0)
    if max_total <= 0:
        return
    root = cfg.cache_dir
    if not os.path.isdir(root):
        return

    items: list[tuple[int, str, str, int]] = []
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".json"):
                continue
            meta_path = os.path.join(dirpath, name)
            bin_path = meta_path[:-5] + ".bin"
            try:
                st = os.stat(bin_path)
            except OSError:
                _best_effort_delete(meta_path, bin_path)
                continue
            meta = _read_meta(meta_path) or {}
            created_at = meta.get("created_at")
            created = int(created_at) if isinstance(created_at, int) else int(st.st_mtime)
            size = int(st.st_size)
            total += size
            items.append((created, meta_path, bin_path, size))

    if total <= max_total:
        return

    items.sort(key=lambda x: x[0])
    for _created, meta_path, bin_path, size in items:
        _best_effort_delete(meta_path, bin_path)
        total -= size
        if total <= max_total:
            break


def scan_proxy_image_cache_stats(*, cfg: ProxyImageCacheConfig) -> tuple[int, int, int]:
    root = str(cfg.cache_dir or "").strip()
    if not root or not os.path.isdir(root):
        return (0, 0, 0)

    total_files = 0
    total_bytes = 0
    stale_files = 0
    now = _now_ts()

    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".json"):
                continue
            meta_path = os.path.join(dirpath, name)
            bin_path = meta_path[:-5] + ".bin"
            try:
                st = os.stat(bin_path)
            except OSError:
                _best_effort_delete(meta_path, bin_path)
                continue

            meta = _read_meta(meta_path) or {}
            expires_at = meta.get("expires_at")
            if isinstance(expires_at, int) and expires_at <= now:
                stale_files += 1

            total_files += 1
            total_bytes += int(st.st_size or 0)

    return (int(total_files), int(total_bytes), int(stale_files))


def purge_expired_proxy_image_cache(*, cfg: ProxyImageCacheConfig) -> tuple[int, int]:
    root = str(cfg.cache_dir or "").strip()
    if not root or not os.path.isdir(root):
        return (0, 0)

    deleted_files = 0
    deleted_bytes = 0
    now = _now_ts()

    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".json"):
                continue
            meta_path = os.path.join(dirpath, name)
            bin_path = meta_path[:-5] + ".bin"
            meta = _read_meta(meta_path) or {}
            expires_at = meta.get("expires_at")
            if not isinstance(expires_at, int) or expires_at > now:
                continue
            try:
                st = os.stat(bin_path)
                deleted_bytes += int(st.st_size or 0)
            except OSError:
                pass
            _best_effort_delete(meta_path, bin_path)
            deleted_files += 1

    return (int(deleted_files), int(deleted_bytes))


def clear_proxy_image_cache(*, cfg: ProxyImageCacheConfig) -> tuple[int, int]:
    root = str(cfg.cache_dir or "").strip()
    if not root or not os.path.isdir(root):
        return (0, 0)

    deleted_files = 0
    deleted_bytes = 0

    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".json"):
                continue
            meta_path = os.path.join(dirpath, name)
            bin_path = meta_path[:-5] + ".bin"
            try:
                st = os.stat(bin_path)
                deleted_bytes += int(st.st_size or 0)
            except OSError:
                pass
            _best_effort_delete(meta_path, bin_path)
            deleted_files += 1

    return (int(deleted_files), int(deleted_bytes))
