from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any, Callable

from app.application.share_listing import ShareListingReader
from app.application.share_validation import ShareLinkValidator
from app.application.share_preview_probe import (
    _BatchAdapters,
    _BatchProbeResult,
    _probe_batch_url,
    _batch_error,
    _should_persist_invalid_share_link,
)

logger = logging.getLogger(__name__)

from cachetools import TTLCache
from sqlalchemy.orm import Session

from app.core.errors import ApiError, not_found
from app.core.settings import settings
from app.core.runtime.tx import end_read_tx
from app.application.container import (
    SessionLocal,
    drive_accounts_repo,
    system_settings_repo,
    adapter_scope,
)
from app.application.container import account_manager_cls as _acc_DatabaseAccountManager

DatabaseAccountManager = _acc_DatabaseAccountManager()
from app.application.container import adapter_registry as _acc_AdapterRegistry

AdapterRegistry = _acc_AdapterRegistry()
from app.application.dto.task_browse import (
    SharePreviewBatchIn,
    SharePreviewBatchItemOut,
    SharePreviewBatchOut,
    ShareValidateOut,
)
from app.application.invalid_share_links import upsert_invalid_share_link
from app.application.share_preview_batch_cache import (
    get_cached_preview_batch_item,
    purge_old_preview_batch_cache,
    upsert_preview_batch_cache,
)


_share_preview_batch_cache = TTLCache(
    maxsize=max(
        1,
        int(
            getattr(settings, "tasks_share_preview_batch_cache_max_entries", 2000)
            or 2000
        ),
    ),
    ttl=max(
        1,
        int(
            getattr(settings, "tasks_share_preview_batch_cache_ttl_seconds", 300) or 300
        ),
    ),
)
_share_preview_batch_cache_lock = threading.Lock()

_validator = ShareLinkValidator()


def _get_validate_cache_ttl() -> int:
    try:
        with SessionLocal() as db:
            value = system_settings_repo().get_value(
                db, "preview_cache_ttl_seconds", ""
            )
            if value:
                return max(30, min(3600, int(value)))
    except Exception:
        pass
    return 300


def _update_validate_cache_ttl() -> None:
    _validator.set_ttl(_get_validate_cache_ttl())


def cache_clear() -> None:
    with _share_preview_batch_cache_lock:
        _share_preview_batch_cache.clear()
    _validator.clear()


def _share_preview_batch_cache_get(*, shareurl: str) -> SharePreviewBatchItemOut | None:
    key = shareurl
    with _share_preview_batch_cache_lock:
        hit = _share_preview_batch_cache.get(key)
    if hit is None:
        return None
    return hit.model_copy()


def _share_preview_batch_cache_set(
    *, shareurl: str, item: SharePreviewBatchItemOut
) -> None:
    key = shareurl
    with _share_preview_batch_cache_lock:
        _share_preview_batch_cache[key] = item


def _short_tx(fn):
    with SessionLocal() as s:
        s.expire_on_commit = False
        try:
            out = fn(s)
            s.commit()
            return out
        except Exception:
            s.rollback()
            raise


def _purge_batch_cache() -> bool:
    try:
        return (
            _short_tx(
                lambda db: purge_old_preview_batch_cache(
                    db,
                    retention_seconds=int(
                        getattr(
                            settings,
                            "tasks_share_preview_batch_db_cache_retention_seconds",
                            7 * 24 * 60 * 60,
                        )
                        or 7 * 24 * 60 * 60
                    ),
                )
            )
            > 0
        )
    except Exception:
        logger.warning("Preview batch cache cleanup failed")
        return False


def _classify_batch_cache(
    shareurls: list[str],
) -> tuple[list[SharePreviewBatchItemOut], dict[str, list[str]], bool]:
    items: list[SharePreviewBatchItemOut] = []
    per_drive: dict[str, list[str]] = {}
    changed = False
    for url in shareurls:
        cached = _share_preview_batch_cache_get(shareurl=url)
        if cached is not None:
            items.append(cached)
            continue
        try:
            row, hit_changed = _short_tx(
                lambda db: get_cached_preview_batch_item(db, shareurl=url)
            )
        except Exception:
            logger.warning("Preview batch persisted cache read failed")
            row, hit_changed = None, False
        if (
            row is not None
            and not bool(row.ok)
            and _should_persist_invalid_share_link(row.message)
        ):
            out = SharePreviewBatchItemOut(
                shareurl=row.shareurl,
                drive_type=row.drive_type,
                ok=False,
                message=row.message,
            )
            items.append(out)
            _share_preview_batch_cache_set(shareurl=url, item=out)
            changed = bool(changed or hit_changed)
            continue
        drive_type = (
            (AdapterRegistry.detect_drive_type(url) or row.drive_type)
            if row is not None
            else AdapterRegistry.detect_drive_type(url)
        )
        if drive_type is None:
            items.append(
                SharePreviewBatchItemOut(
                    shareurl=url,
                    drive_type=None,
                    ok=False,
                    message="无法识别的网盘分享链接",
                )
            )
        else:
            per_drive.setdefault(str(drive_type), []).append(url)
    return items, per_drive, changed


def _batch_candidate_names(
    db: Session, drive_type: str, specified_name: str | None
) -> list[str]:
    rows = drive_accounts_repo().list_enabled_by_type(db, drive_type=drive_type)
    rows.sort(
        key=lambda row: (
            0 if bool(getattr(row, "is_default", False)) else 1,
            0 if str(getattr(row, "runtime_status", "") or "") == "active" else 1,
            int(getattr(row, "id", 0) or 0),
        )
    )
    if specified_name:
        specified = str(specified_name or "").strip()
        if specified:
            hit = next((row for row in rows if str(row.name) == specified), None)
            if hit is None:
                raise not_found("DRIVE_ACCOUNT_NOT_FOUND", "指定账号不存在或不可用")
            rows = [hit] + [row for row in rows if str(row.name) != specified]
    return [str(getattr(row, "name", "") or "").strip() for row in rows]


def _persist_batch_result(
    out: SharePreviewBatchItemOut, *, confirmed_invalid: bool = False
) -> tuple[bool, bool]:
    """Only independent short writes; a database fault never repeats drive IO."""
    cache_changed = invalid_changed = False
    if not out.ok and not confirmed_invalid:
        return False, False
    try:
        cache_changed = bool(
            _short_tx(
                lambda db: upsert_preview_batch_cache(
                    db,
                    shareurl=out.shareurl,
                    drive_type=out.drive_type,
                    ok=out.ok,
                    message=out.message,
                    ttl_seconds=int(
                        getattr(
                            settings,
                            "tasks_share_preview_batch_db_cache_ttl_seconds",
                            6 * 60 * 60,
                        )
                        or 6 * 60 * 60
                    ),
                )
            )
        )
    except Exception:
        logger.warning("Preview batch cache write failed")
    if not out.ok and confirmed_invalid:
        try:
            invalid_changed = bool(
                _short_tx(
                    lambda db: upsert_invalid_share_link(
                        db,
                        shareurl=out.shareurl,
                        drive_type=out.drive_type,
                        message=out.message,
                    )
                )
            )
        except Exception:
            logger.warning("Preview batch invalid-link write failed")
    return cache_changed, invalid_changed


def _read_batch_accounts(
    db: Session, payload: SharePreviewBatchIn, per_drive: dict[str, list[str]]
) -> tuple[_BatchAdapters, dict[str, list[str]], dict[str, str]]:
    manager = DatabaseAccountManager(db, no_login=True)
    candidates: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    for drive in per_drive:
        try:
            candidates[drive] = _batch_candidate_names(db, drive, payload.account_name)
        except Exception as error:
            errors[drive] = _batch_error(error)
    # Account names are detached before ending the read transaction; external
    # stages cannot lazily reload ORM attributes on the request Session.
    end_read_tx(db)
    return _BatchAdapters(manager), candidates, errors


def _preview_share_batch(
    db: Session, payload: SharePreviewBatchIn
) -> tuple[SharePreviewBatchOut, bool]:
    shareurls = list(
        dict.fromkeys(
            str(url or "").strip()
            for url in (payload.shareurls or [])
            if str(url or "").strip()
        )
    )
    if not shareurls:
        return SharePreviewBatchOut(items=[]), False
    if db.new or db.dirty or db.deleted:
        raise RuntimeError("Batch preview requires a read-only session")
    end_read_tx(db)
    cache_changed = _purge_batch_cache()
    items, per_drive, cache_hit_changed = _classify_batch_cache(shareurls)
    cache_changed = bool(cache_changed or cache_hit_changed)
    if not per_drive:
        return SharePreviewBatchOut(items=items), cache_changed
    readers, candidates, errors = _read_batch_accounts(db, payload, per_drive)
    invalid_changed = False
    for drive, urls in per_drive.items():
        if drive in errors or not candidates.get(drive):
            message = errors.get(drive, "没有可用的驱动账号")
            items.extend(
                SharePreviewBatchItemOut(
                    shareurl=url, drive_type=drive, ok=False, message=message
                )
                for url in urls
            )
            continue
        for url in urls:
            try:
                result = _probe_batch_url(url, drive, candidates[drive], readers)
            except Exception as error:
                result = _BatchProbeResult(
                    SharePreviewBatchItemOut(
                        shareurl=url,
                        drive_type=drive,
                        ok=False,
                        message=_batch_error(error),
                    )
                )
            out = result.item
            items.append(out)
            if out.ok or result.confirmed_invalid:
                _share_preview_batch_cache_set(shareurl=url, item=out)
            saved_cache, saved_invalid = _persist_batch_result(
                out, confirmed_invalid=result.confirmed_invalid
            )
            cache_changed = bool(cache_changed or saved_cache)
            invalid_changed = bool(invalid_changed or saved_invalid)
            time.sleep(random.uniform(0.06, 0.22))
    return SharePreviewBatchOut(items=items), bool(invalid_changed or cache_changed)


def validate_share_links_streaming(
    db: Session, shareurls: list[str], *, release_read: Callable[[], None] | None = None
):
    _update_validate_cache_ttl()
    from app.application.system_settings import get_setting

    yield from _validator.stream(
        db,
        shareurls,
        registry=AdapterRegistry,
        accounts=drive_accounts_repo(),
        manager_factory=DatabaseAccountManager,
        read_setting=get_setting,
        release_read=release_read,
    )


def validate_share_links(
    db: Session, shareurls: list[str], *, release_read: Callable[[], None] | None = None
) -> ShareValidateOut:
    """轻量级验证：只检查链接有效性 + 提取分享者信息，不做视频解析。"""
    from app.application.dto.task_browse import ShareValidateOut

    items = list(
        validate_share_links_streaming(
            db,
            shareurls,
            **({"release_read": release_read} if release_read is not None else {}),
        )
    )
    return ShareValidateOut(items=items)


def _prepare_listing(
    db: Session,
    shareurl: str,
    account_name: str | None,
    release_read: Callable[[], None] | None,
) -> ShareListingReader | None:
    drive_type = AdapterRegistry.detect_drive_type(shareurl)
    if drive_type is None:
        return None

    if not account_name:
        rows = drive_accounts_repo().list_enabled_by_type(db, drive_type=drive_type)
        rows.sort(
            key=lambda x: (
                0 if bool(getattr(x, "is_default", False)) else 1,
                0 if str(getattr(x, "runtime_status", "") or "") == "active" else 1,
                int(getattr(x, "id", 0) or 0),
            )
        )
        if not rows:
            return None
        account_name = str(getattr(rows[0], "name", "") or "").strip()

    manager = DatabaseAccountManager(db, no_login=True)
    task_payload = {"shareurl": shareurl, "account_name": account_name}
    adapter = manager.get_adapter_for_task(task_payload, allow_inactive=True)
    if adapter is None:
        return None

    if release_read is not None:
        release_read()

    if not bool(getattr(adapter, "is_active", False)):
        try:
            adapter.init()
        except Exception:
            return None

    try:
        pwd_id, passcode, extracted_pdir_fid, _ = adapter.extract_url(shareurl)
    except Exception:
        return None

    if not pwd_id:
        return None

    try:
        token_response = adapter.get_stoken(pwd_id, passcode or "")
    except Exception:
        return None

    stoken = ((token_response or {}).get("data") or {}).get("stoken")
    if not stoken:
        return None

    return ShareListingReader(adapter, pwd_id, stoken, extracted_pdir_fid)


def fetch_share_file_list(
    db: Session,
    shareurl: str,
    account_name: str | None = None,
    max_depth: int = 3,
    folder_filter: str = "",
    folder_exclude: str = "",
    folder_filter_mode: str = "",
    folder_exclude_mode: str = "",
    *,
    release_read: Callable[[], None] | None = None,
):
    with adapter_scope():
        reader = _prepare_listing(db, shareurl, account_name, release_read)
        return (
            reader.files(max_depth, folder_exclude, folder_exclude_mode)
            if reader
            else ([], False)
        )


def fetch_share_file_list_grouped(
    db: Session,
    shareurl: str,
    account_name: str | None = None,
    max_depth: int = 3,
    folder_filter: str = "",
    folder_exclude: str = "",
    folder_filter_mode: str = "",
    folder_exclude_mode: str = "",
    folder_priority: str = "",
    folder_priority_mode: str = "",
    *,
    release_read: Callable[[], None] | None = None,
):
    with adapter_scope():
        reader = _prepare_listing(db, shareurl, account_name, release_read)
        return (
            reader.groups(
                max_depth,
                folder_filter,
                folder_exclude,
                folder_filter_mode,
                folder_exclude_mode,
                folder_priority,
                folder_priority_mode,
            )
            if reader
            else []
        )


def fetch_share_root_dirs(
    db: Session,
    shareurl: str,
    account_name: str | None = None,
    *,
    release_read: Callable[[], None] | None = None,
) -> list[tuple[str, str]]:
    with adapter_scope():
        reader = _prepare_listing(db, shareurl, account_name, release_read)
        return reader.root_dirs() if reader else []


def preview_share_batch(
    db: Session, payload: SharePreviewBatchIn
) -> tuple[SharePreviewBatchOut, bool]:
    with adapter_scope():
        return _preview_share_batch(db, payload)
