"""Bounded streaming link validation with caller-supplied account dependencies."""

from __future__ import annotations
import threading
from typing import Any, Callable, Iterator
from app.core.runtime.ttl_cache import TtlCache
from sqlalchemy.orm import Session
from app.application.dto.task_browse import ShareValidateItemOut
from app.application.drive_account_types import AccountRegistry, AccountRepository


class ShareLinkValidator:
    def __init__(self, ttl_seconds: int = 300) -> None:
        self._ttl = ttl_seconds
        self._cache = TtlCache(maxsize=2000, ttl_seconds=ttl_seconds)
        self._lock = threading.Lock()

    def set_ttl(self, ttl_seconds: int) -> None:
        with self._lock:
            if ttl_seconds != self._ttl:
                self._ttl = ttl_seconds
                self._cache = TtlCache(maxsize=2000, ttl_seconds=ttl_seconds)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def stream(
        self,
        db: Session,
        shareurls: list[str],
        *,
        registry: AccountRegistry,
        accounts: AccountRepository,
        manager_factory: Callable[..., Any],
        read_setting: Callable[[Session, str], str],
        release_read: Callable[[], None] | None = None,
    ) -> Iterator[ShareValidateItemOut]:
        """生成器版本：验证完一个链接立即返回结果，不等全部完成。"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        urls = [str(x or "").strip() for x in shareurls]
        urls = [x for x in urls if x]
        urls = list(dict.fromkeys(urls))
        if not urls:
            return

        # 缓存查找
        cached_items: list[ShareValidateItemOut] = []
        uncached_urls: list[str] = []
        with self._lock:
            for url in urls:
                hit = self._cache.get(url)
                if hit is not None:
                    cached_items.append(hit)
                else:
                    uncached_urls.append(url)

        for item in cached_items:
            yield item

        if not uncached_urls:
            return

        def _safe_error(e: Exception) -> str:
            text = f"{type(e).__name__}: {str(e)}"
            return text.strip() or type(e).__name__

        # 非夸克网盘直接返回，无需验证
        per_drive: dict[str, list[str]] = {}
        for url in uncached_urls:
            drive_type = registry.detect_drive_type(url)
            if drive_type is None:
                yield ShareValidateItemOut(
                    shareurl=url, ok=False, message="无法识别的网盘分享链接"
                )
                continue
            # 非夸克网盘直接返回，无需验证（避免多线程 session 冲突）
            if drive_type == "quark":
                per_drive.setdefault(str(drive_type), []).append(url)
            else:
                yield ShareValidateItemOut(shareurl=url, ok=True)

        if not per_drive:
            return

        # 预先查询所有可用账号（避免线程内访问数据库）
        accounts_by_drive: dict[str, list[str]] = {}
        for drive_type in per_drive:
            rows = list(accounts.list_enabled_by_type(db, drive_type=drive_type))
            rows.sort(
                key=lambda x: (
                    0 if bool(getattr(x, "is_default", False)) else 1,
                    0 if str(getattr(x, "runtime_status", "") or "") == "active" else 1,
                    int(getattr(x, "id", 0) or 0),
                )
            )
            accounts_by_drive[drive_type] = [
                str(row.name or "").strip() for row in rows
            ]

        manager = manager_factory(db, no_login=True)
        try:
            try:
                batch_size = int(
                    str(read_setting(db, "validate_batch_size") or "5").strip()
                )
            except (TypeError, ValueError):
                batch_size = 5
            batch_size = max(1, min(batch_size, 20))
            if release_read is not None:
                release_read()

            def _validate_single_url(url: str, drive_type: str) -> ShareValidateItemOut:
                """验证单个链接"""
                candidates = accounts_by_drive.get(drive_type, [])
                if not candidates:
                    return ShareValidateItemOut(
                        shareurl=url, ok=False, message="没有可用的驱动账号"
                    )

                ok = False
                author_name = ""
                last_error: str | None = None
                for name in candidates:
                    if not name:
                        last_error = "账号名称为空"
                        continue
                    adapter = manager.manager.get_adapter(name)
                    if adapter is None:
                        last_error = f"账号 {name}: 不可用"
                        continue
                    if (not bool(getattr(adapter, "is_active", False))) and (
                        not bool(getattr(adapter, "no_login", False))
                    ):
                        try:
                            init_ok = adapter.init()
                        except Exception:
                            init_ok = None
                        if not init_ok:
                            last_error = f"账号 {name}: 初始化失败"
                            continue
                    try:
                        pwd_id, passcode, pdir_fid, _ = adapter.extract_url(url)
                    except Exception as e:
                        last_error = _safe_error(e)
                        break
                    if not pwd_id:
                        last_error = "无法解析分享链接"
                        break
                    try:
                        token_response = adapter.get_stoken(pwd_id, passcode or "")
                    except Exception as e:
                        last_error = f"账号 {name}: {_safe_error(e)}"
                        continue
                    stoken = ((token_response or {}).get("data") or {}).get("stoken")
                    author_obj = ((token_response or {}).get("data") or {}).get(
                        "author"
                    )
                    if isinstance(author_obj, dict):
                        author_name = (
                            author_obj.get("nick_name")
                            or author_obj.get("nickname")
                            or author_obj.get("user_name")
                            or author_obj.get("name")
                            or ""
                        ).strip()
                    if not stoken:
                        message = (token_response or {}).get(
                            "message"
                        ) or "获取分享 token 失败"
                        last_error = f"账号 {name}: {str(message)}"
                        continue
                    try:
                        detail = adapter.get_detail(pwd_id, stoken, pdir_fid or "")
                    except Exception as e:
                        last_error = f"账号 {name}: {_safe_error(e)}"
                        continue
                    raw_items = (((detail or {}).get("data") or {}).get("list")) or []
                    if raw_items:
                        ok = True
                        break
                    else:
                        last_error = (
                            detail.get("message")
                            if isinstance(detail, dict) and detail.get("message")
                            else "链接内容为空"
                        )
                        continue

                result = ShareValidateItemOut(
                    shareurl=url,
                    ok=ok,
                    share_author_name=author_name or None,
                    message=None if ok else (last_error or "没有可用账号"),
                )
                # 缓存
                with self._lock:
                    self._cache.set(url, result)
                return result

            # 并行验证，完成一个立即返回一个
            for drive_type, dt_urls in per_drive.items():
                if not accounts_by_drive.get(drive_type):
                    for url in dt_urls:
                        yield ShareValidateItemOut(
                            shareurl=url, ok=False, message="没有可用的驱动账号"
                        )
                    continue
                with ThreadPoolExecutor(max_workers=batch_size) as executor:
                    future_map = {
                        executor.submit(_validate_single_url, url, drive_type): url
                        for url in dt_urls
                    }
                    for future in as_completed(future_map):
                        try:
                            result = future.result()
                            yield result
                        except Exception as e:
                            url = future_map[future]
                            yield ShareValidateItemOut(
                                shareurl=url, ok=False, message=_safe_error(e)
                            )

        finally:
            close = getattr(manager, "close", None)
            if callable(close):
                close()
