"""Read-only share probing; no database sessions or persistence."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any
from app.core.errors import ApiError
from app.application.dto.task_browse import (
    SharePreviewBatchItemOut,
    SharePreviewBatchLatestOut,
)
from app.domain.share_entries import (
    _bool_is_dir,
    _pick_name,
    _pick_fid,
    _pick_updated_at,
    _pick_size,
    _is_video_name,
    _to_ts,
)

_CONFIRMED_INVALID = "分享链接已确认失效"
_TRANSIENT_FAILURE = "网盘响应异常，可能为网络或账号状态问题，请稍后重试"


def _should_persist_invalid_share_link(message: str | None) -> bool:
    # Historical free-text negatives cannot establish permanent link failure.
    return message == _CONFIRMED_INVALID


class _ShareReadError(Exception):
    def __init__(self, kind: str = "transient"):
        self.kind = kind
        super().__init__(
            {
                "invalid": _CONFIRMED_INVALID,
                "auth": "账号登录状态异常，请重新认证",
                "passcode": "分享提取码缺失或错误",
            }.get(kind, _TRANSIENT_FAILURE)
        )


def _response_succeeded(response: Any) -> bool:
    return (
        isinstance(response, dict)
        and response.get("code") in (None, 0, "0")
        and response.get("status") in (None, 200, "200")
    )


def _token_failure(adapter: Any, response: Any) -> _ShareReadError:
    if not isinstance(response, dict) or response.get("status") not in (
        None,
        200,
        "200",
        400,
        "400",
    ):
        return _ShareReadError()
    classify = getattr(adapter, "classify_share_failure", None)
    if callable(classify):
        kind = classify(response).get("kind")
        # Require a documented provider code, never an arbitrary error message.
        if kind == "invalid" and str(response.get("code")) in {
            str(code) for code in getattr(adapter, "SHARE_INVALID_CODES", ())
        }:
            return _ShareReadError("invalid")
        if kind in ("auth", "passcode"):
            return _ShareReadError(kind)
    return _ShareReadError()


@dataclass
class _BatchProbeResult:
    item: SharePreviewBatchItemOut
    confirmed_invalid: bool = False


def _auto_resolve_latest_video(
    adapter: Any, pwd_id: str, stoken: str, start_fid: str
) -> tuple[str, dict[str, Any] | None]:
    current_fid = str(start_fid or "").strip()
    latest: dict[str, Any] | None = None
    for _depth in range(10):
        detail = adapter.get_detail(pwd_id, stoken, current_fid)
        if not _response_succeeded(detail) or not isinstance(detail.get("data"), dict):
            raise _ShareReadError()
        raw_items = detail["data"].get("list")
        if not isinstance(raw_items, list) or any(
            not isinstance(item, dict) for item in raw_items
        ):
            raise _ShareReadError()
        files = [x for x in raw_items if not _bool_is_dir(x)]
        videos = [x for x in files if _is_video_name(_pick_name(x))]
        if videos:
            videos.sort(key=lambda x: _to_ts(_pick_updated_at(x)) or 0, reverse=True)
            hit = videos[0]
            latest = {
                "fid": _pick_fid(hit) or None,
                "name": _pick_name(hit) or None,
                "updated_at": _pick_updated_at(hit),
                "size": _pick_size(hit),
            }
            break
        dirs = [x for x in raw_items if _bool_is_dir(x)]
        if not dirs:
            break
        if len(dirs) > 1:
            dirs.sort(key=lambda x: _to_ts(_pick_updated_at(x)) or 0, reverse=True)
        next_dir = dirs[0]
        next_fid = str(_pick_fid(next_dir) or "").strip()
        if not next_fid or next_fid == current_fid:
            break
        current_fid = next_fid
    return current_fid, latest


class _BatchAdapters:
    """Adapter IO and per-batch instance reuse; receives no request Session."""

    def __init__(self, manager: Any):
        self.manager = manager
        self.cache: dict[str, Any] = {}

    def get_ready(self, name: str) -> Any:
        if not name:
            return None
        if name in self.cache:
            return self.cache[name]
        adapter = self.manager.manager.get_adapter(name)
        if adapter is None:
            return None
        if not bool(getattr(adapter, "is_active", False)) and not bool(
            getattr(adapter, "no_login", False)
        ):
            try:
                if not adapter.init():
                    return None
            except Exception:
                return None
        self.cache[name] = adapter
        return adapter


def _probe_batch_url(
    url: str, drive_type: str, names: list[str], readers: _BatchAdapters
) -> _BatchProbeResult:
    last_error: str | None = None
    confirmed_invalid = bool(names)
    author_name = ""
    for name in names:
        adapter = readers.get_ready(name)
        if adapter is None:
            confirmed_invalid = False
            last_error = f"账号 {name}: 不可用"
            continue
        try:
            pwd_id, passcode, extracted_fid, _ = adapter.extract_url(url)
        except Exception as error:
            confirmed_invalid = False
            last_error = _batch_error(error)
            break
        if not pwd_id:
            confirmed_invalid = False
            last_error = "无法解析分享链接"
            break
        try:
            token = adapter.get_stoken(pwd_id, passcode or "")
        except Exception as error:
            confirmed_invalid = False
            last_error = f"账号 {name}: {_batch_error(error)}"
            continue
        data = token.get("data") if isinstance(token, dict) else None
        data = data if isinstance(data, dict) else {}
        stoken = data.get("stoken")
        author = data.get("author")
        author_name = (
            (
                (
                    author.get("nick_name")
                    or author.get("nickname")
                    or author.get("user_name")
                    or author.get("name")
                    or ""
                ).strip()
            )
            if isinstance(author, dict)
            else ""
        )
        if not stoken or not _response_succeeded(token):
            failure = _token_failure(adapter, token)
            confirmed_invalid = confirmed_invalid and failure.kind == "invalid"
            last_error = str(failure)
            continue
        try:
            folder_id, latest = _auto_resolve_latest_video(
                adapter, pwd_id, stoken, extracted_fid or ""
            )
        except Exception as error:
            confirmed_invalid = False
            last_error = f"账号 {name}: {_batch_error(error)}"
            continue
        return _BatchProbeResult(
            SharePreviewBatchItemOut(
                shareurl=url,
                drive_type=drive_type,
                ok=True,
                suggested_account_name=name or None,
                pdir_fid=folder_id,
                resolved_pdir_fid=folder_id,
                latest_video=SharePreviewBatchLatestOut.model_validate(latest)
                if latest is not None
                else None,
                share_author_name=author_name,
            )
        )
    return _BatchProbeResult(
        SharePreviewBatchItemOut(
            shareurl=url,
            drive_type=drive_type,
            ok=False,
            message=_CONFIRMED_INVALID
            if confirmed_invalid
            else (last_error or "没有可用账号"),
        ),
        confirmed_invalid,
    )


def _batch_error(error: Exception) -> str:
    if isinstance(error, ApiError):
        return error.message
    # Exception strings can contain cookies or request URLs. Unknown IO faults
    # also cannot establish that a share is permanently invalid.
    return "网盘响应异常，可能为网络或账号状态问题，请稍后重试"
