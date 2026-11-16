from __future__ import annotations


import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.application.share_preview import preview_share as post_share_preview
from app.core.errors import bad_request, not_found
from app.application.container import adapter_factory as _acc_AdapterFactory; AdapterFactory = _acc_AdapterFactory()
from app.application.container import account_manager_cls as _acc_DatabaseAccountManager; DatabaseAccountManager = _acc_DatabaseAccountManager()
from app.application.container import adapter_registry as _acc_AdapterRegistry; AdapterRegistry = _acc_AdapterRegistry()
from app.application.container import tasks_repo
from app.application.magic_regex import list_enabled_effective_rules_for_picker
from app.application.media_discovery import tmdb_detail, tmdb_search
from app.application.share_preview_batch import preview_share_batch
from app.application.resource_search import fetch_task_suggestions
from app.application.dto.task_browse import SharePreviewBatchIn, SharePreviewIn


from app.application.telegram_bot.action_helpers import _all_http_urls, _bool_is_dir, _first_http_url, _format_display_datetime, _get_active_account, _pick_any_default_account, _pick_default_account_name, _pick_fid, _pick_name, _pick_size, _pick_updated_at, _rewrite_shareurl_with_fid, _task_to_dict, _tmdb_display_date, _tmdb_display_title, _tmdb_standard_keyword, _to_sort_ts, _tv_progress_text

@dataclass
class TelegramResourceActions:
    db: Session

    def search_resources(self, keyword: str, *, drive_type: str | None = None) -> dict[str, Any]:
        items, changed, message = fetch_task_suggestions(self.db, keyword=keyword, deep=0, drive_type=drive_type)
        verified_items: list[dict[str, Any]] = [dict(x) for x in items]
        shareurls = [str(x.get("shareurl") or "").strip() for x in items if str(x.get("shareurl") or "").strip()]
        if shareurls:
            preview_map: dict[str, Any] = {}
            batch_size = 50
            preview_failed = False
            for offset in range(0, len(shareurls), batch_size):
                chunk = shareurls[offset : offset + batch_size]
                if not chunk:
                    continue
                try:
                    batch_out, preview_changed = preview_share_batch(self.db, SharePreviewBatchIn(shareurls=chunk, account_name=None))
                except Exception:
                    preview_failed = True
                    continue
                if preview_changed:
                    changed = True
                for row in batch_out.items or []:
                    key = str(row.shareurl or "").strip()
                    if key:
                        preview_map[key] = row
            ok_count = 0
            fail_count = 0
            for item in verified_items:
                shareurl = str(item.get("shareurl") or "").strip()
                if not shareurl:
                    continue
                preview_item = preview_map.get(shareurl)
                if preview_item is None:
                    item["verify"] = False
                    fail_count += 1
                    continue
                item["verify"] = bool(preview_item.ok)
                item["pdir_fid"] = str(preview_item.pdir_fid or preview_item.resolved_pdir_fid or "") or None
                item["resolved_pdir_fid"] = str(preview_item.resolved_pdir_fid or preview_item.pdir_fid or "") or None
                item["latest_video"] = preview_item.latest_video.model_dump(mode="json") if preview_item.latest_video is not None else None
                item["suggested_account_name"] = str(preview_item.suggested_account_name or "") or None
                if item["verify"]:
                    ok_count += 1
                else:
                    fail_count += 1
            max_size = max([int((x.get("latest_video") or {}).get("size") or 0) for x in verified_items] or [0])
            for item in verified_items:
                size = int((item.get("latest_video") or {}).get("size") or 0)
                item["max_video"] = bool(max_size > 0 and size == max_size)
            verified_items.sort(
                key=lambda x: (
                    -(int((x.get("latest_video") or {}).get("size") or 0)),
                    str(x.get("taskname") or ""),
                )
            )
            if ok_count or fail_count:
                extra = f"链接校验: 可用 {ok_count} / 不可用 {fail_count}"
                message = f"{message}; {extra}" if message else extra
            if preview_failed:
                extra = "部分链接校验失败，已跳过异常批次"
                message = f"{message}; {extra}" if message else extra
        else:
            verified_items = [dict(x) for x in items]
        if changed:
            self.db.commit()
        return {"items": verified_items, "message": message}


    def inspect_share_text(self, text: str) -> dict[str, Any] | None:
        raw_text = str(text or "").strip()
        if not raw_text:
            return None
        candidates = self.extract_share_candidates(raw_text)
        if not candidates:
            return None
        picked = candidates[0]
        return self.inspect_share_candidate(raw_text, str(picked.get("shareurl") or raw_text))


    def extract_share_candidates(self, text: str) -> list[dict[str, Any]]:
        raw_text = str(text or "").strip()
        if not raw_text:
            return []
        urls = _all_http_urls(raw_text)
        candidates: list[dict[str, Any]] = []
        for url in urls:
            drive_type = AdapterRegistry.detect_drive_type(url)
            if drive_type is None:
                continue
            candidates.append({"shareurl": url, "drive_type": drive_type})
        if candidates:
            return candidates
        drive_type = AdapterRegistry.detect_drive_type(raw_text)
        if drive_type is None:
            return []
        extracted_url = _first_http_url(raw_text) or raw_text
        return [{"shareurl": extracted_url, "drive_type": drive_type}]


    def inspect_share_candidate(self, text: str, shareurl: str) -> dict[str, Any] | None:
        raw_text = str(text or "").strip()
        picked_shareurl = str(shareurl or "").strip()
        if not raw_text or not picked_shareurl:
            return None
        drive_type = AdapterRegistry.detect_drive_type(picked_shareurl) or AdapterRegistry.detect_drive_type(raw_text)
        if drive_type is None:
            return None
        extracted_url = picked_shareurl
        adapter = AdapterFactory.create_adapter(str(drive_type), no_login=True, account_name="tg_share_text")
        pwd_id: str | None = None
        passcode = ""
        extracted_pdir_fid: Any = None
        if adapter is not None:
            try:
                pwd_id, passcode, extracted_pdir_fid, _ = adapter.extract_url(raw_text)
            except Exception:
                pwd_id = None
                passcode = ""
                extracted_pdir_fid = None
        if not pwd_id:
            return {
                "raw_text": raw_text,
                "shareurl": extracted_url,
                "drive_type": drive_type,
                "parsed": False,
                "preview_ok": False,
                "preview_message": "无法解析分享链接",
                "matched_task": None,
                "suggested_account_name": None,
                "resolved_pdir_fid": None,
                "latest_video": None,
            }

        shareurl_value = extracted_url
        preview_changed = False
        preview_item = None
        try:
            preview_out, preview_changed = preview_share_batch(self.db, SharePreviewBatchIn(shareurls=[raw_text], account_name=None))
            preview_item = (preview_out.items or [None])[0]
        except Exception as exc:
            preview_item = {"ok": False, "message": str(exc)}
        if preview_changed:
            self.db.commit()

        candidate_urls = [raw_text, extracted_url, shareurl_value]
        candidate_urls = [str(x).strip() for x in candidate_urls if str(x).strip()]
        candidate_urls = list(dict.fromkeys(candidate_urls))
        task = None
        if candidate_urls:
            task = tasks_repo().get_first_by_shareurls(self.db, candidate_urls)

        latest_video: dict[str, Any] | None = None
        if preview_item is not None:
            latest_raw = getattr(preview_item, "latest_video", None)
            if latest_raw is not None:
                latest_video = latest_raw.model_dump(mode="json")
        return {
            "raw_text": raw_text,
            "shareurl": shareurl_value,
            "resolved_shareurl": _rewrite_shareurl_with_fid(shareurl_value, getattr(preview_item, "resolved_pdir_fid", None) or str(extracted_pdir_fid or "") or None),
            "drive_type": drive_type,
            "parsed": True,
            "pwd_id": pwd_id,
            "passcode": passcode,
            "preview_ok": bool(getattr(preview_item, "ok", False)),
            "preview_message": getattr(preview_item, "message", None),
            "suggested_account_name": getattr(preview_item, "suggested_account_name", None),
            "resolved_pdir_fid": getattr(preview_item, "resolved_pdir_fid", None) or str(extracted_pdir_fid or "") or None,
            "latest_video": latest_video,
            "matched_task": _task_to_dict(self.db, task) if task is not None else None,
        }


    def preview_task_share(
        self,
        *,
        shareurl: str,
        account_name: str | None = None,
        pdir_fid: str | None = None,
        max_items: int = 200,
        taskname: str | None = None,
        pattern: str | None = None,
        replace: str | None = None,
        savepath: str | None = None,
        ignore_extension: bool | None = None,
        tmdb_id: int | None = None,
        tmdb_media_type: str | None = None,
    ) -> dict[str, Any]:
        payload = SharePreviewIn(
            shareurl=str(shareurl or "").strip(),
            account_name=str(account_name or "").strip() or None,
            pdir_fid=str(pdir_fid or "").strip() or None,
            max_items=int(max_items or 200),
            taskname=str(taskname or "").strip() or None,
            pattern=str(pattern or "").strip() or None,
            replace=str(replace or "").strip() or None,
            savepath=str(savepath or "").strip() or None,
            ignore_extension=ignore_extension,
            tmdb_id=tmdb_id,
            tmdb_media_type=str(tmdb_media_type or "").strip() or None,
        )
        out = post_share_preview(payload, db=self.db)
        data = out.model_dump(mode="json")
        resolved_fid = str(data.get("pdir_fid") or "").strip()
        data["shareurl"] = payload.shareurl
        data["resolved_shareurl"] = _rewrite_shareurl_with_fid(payload.shareurl, resolved_fid or payload.pdir_fid)
        items = list(data.get("items") or [])
        for item in items:
            if not isinstance(item, dict):
                continue
            item["updated_at_display"] = _format_display_datetime(item.get("updated_at"))
        items.sort(key=lambda x: (not bool(x.get("is_dir")), -_to_sort_ts(x.get("updated_at")), str(x.get("name") or "").lower()))
        data["items"] = items
        return data


    def search_tmdb_media(self, keyword: str, *, media_type: str = "multi", year: str | None = None) -> dict[str, Any]:
        configured, items, page_no, total_pages, total_results = tmdb_search(
            self.db, q=keyword, search_type=media_type, page=1, year=year
        )
        rows: list[dict[str, Any]] = []
        for item in items:
            row = dict(item)
            mt = str(row.get("media_type") or "").strip().lower()
            tid = int(row.get("id") or 0) or 0
            detail_data: dict[str, Any] = {}
            update_weekdays: list[int] = []
            episode_weekdays: list[int] = []
            if configured and mt in {"movie", "tv"} and tid > 0:
                try:
                    _ok, detail_data, update_weekdays, episode_weekdays = tmdb_detail(self.db, media_type=mt, tmdb_id=tid)  # type: ignore[arg-type]
                except Exception:
                    detail_data = {}
                    update_weekdays = []
                    episode_weekdays = []
            row["display_title"] = _tmdb_display_title(row)
            row["display_date"] = _tmdb_display_date(row)
            row["standard_keyword"] = _tmdb_standard_keyword(row)
            row["detail"] = detail_data
            row["progress_text"] = _tv_progress_text(detail_data) if mt == "tv" else ""
            row["update_weekdays"] = update_weekdays
            row["episode_weekdays"] = episode_weekdays
            rows.append(row)
        self.db.commit()
        return {
            "configured": configured,
            "items": rows,
            "page": page_no,
            "total_pages": total_pages,
            "total_results": total_results,
        }


    def list_magic_regex_rules(self) -> list[dict[str, str]]:
        return [dict(item) for item in list_enabled_effective_rules_for_picker(self.db)]


    def browse_task_drive(
        self,
        *,
        dir_path: str,
        account_name: str | None = None,
        shareurl: str | None = None,
        max_items: int = 100,
    ) -> dict[str, Any]:
        drive_type: str | None = None
        chosen_account = str(account_name or "").strip() or None
        if chosen_account:
            account = _get_active_account(self.db, chosen_account)
            if account is None:
                raise not_found("DRIVE_ACCOUNT_NOT_FOUND", "指定账号不存在或不可用")
            drive_type = str(account.drive_type)
        if not chosen_account:
            if shareurl:
                drive_type = AdapterRegistry.detect_drive_type(str(shareurl))
                if drive_type is None:
                    raise bad_request("TASK_SHAREURL_INVALID", "无法识别的网盘分享链接")
                chosen_account = _pick_default_account_name(self.db, drive_type)
            else:
                any_default = _pick_any_default_account(self.db)
                if any_default is not None:
                    chosen_account = str(any_default.name)
                    drive_type = str(any_default.drive_type)
        if not chosen_account:
            raise not_found("DRIVE_ACCOUNT_NOT_FOUND", "没有可用的驱动账号")

        manager = DatabaseAccountManager(self.db)
        task_payload = {"shareurl": shareurl or "", "account_name": chosen_account}
        manager.init_for_tasks([task_payload])
        adapter = manager.get_adapter_for_task(task_payload)
        if adapter is None:
            raise not_found("DRIVE_ACCOUNT_NOT_FOUND", "没有可用的驱动账号")

        raw_dir_path = str(dir_path or "").strip() or "/"
        is_fid_mode = ("/" not in raw_dir_path) and (raw_dir_path not in ("/", "0"))
        normalized_path = re.sub(r"/+", "/", raw_dir_path)
        if not normalized_path.startswith("/") and not is_fid_mode:
            normalized_path = "/" + normalized_path
        normalized_path = normalized_path.rstrip("/") or "/"

        paths: list[dict[str, str]] = []
        if raw_dir_path in ("/", "0"):
            pdir_fid = "0"
        elif is_fid_mode:
            pdir_fid = raw_dir_path
        else:
            fid_list = adapter.get_fids([normalized_path]) or []
            match = None
            for item in fid_list:
                item_path = item.get("file_path") or item.get("path") or item.get("filePath")
                if item_path == normalized_path:
                    match = item
                    break
            if match is None and fid_list:
                match = fid_list[0]
            pdir_fid = str(match.get("fid")) if match and match.get("fid") else None

            segments = [s for s in normalized_path.split("/") if s]
            if segments:
                accum_paths = ["/" + "/".join(segments[: i + 1]) for i in range(len(segments))]
                fid_arr = adapter.get_fids(accum_paths) or []
                fid_map: dict[str, str] = {}
                for item in fid_arr:
                    p = item.get("file_path") or item.get("path") or item.get("filePath")
                    f = item.get("fid")
                    if p and f:
                        fid_map[str(p)] = str(f)
                for i, name in enumerate(segments):
                    fid_val = fid_map.get(accum_paths[i])
                    if fid_val:
                        paths.append({"fid": fid_val, "name": name})

        if not pdir_fid:
            return {
                "account_name": chosen_account,
                "drive_type": drive_type,
                "dir_path": raw_dir_path,
                "exists": False,
                "pdir_fid": None,
                "items": [],
                "paths": paths,
            }

        listing = adapter.ls_dir(str(pdir_fid), max_items=int(max_items))
        raw_items = (((listing or {}).get("data") or {}).get("list")) or []
        items: list[dict[str, Any]] = []
        for raw in raw_items[: int(max_items)]:
            fid = _pick_fid(raw)
            name = _pick_name(raw)
            if not fid or not name:
                continue
            items.append(
                {
                    "fid": fid,
                    "name": name,
                    "is_dir": _bool_is_dir(raw),
                    "updated_at": _pick_updated_at(raw),
                    "size": _pick_size(raw),
                    "include_items": raw.get("children_count") or raw.get("include_items"),
                }
            )
        items.sort(key=lambda x: (not bool(x.get("is_dir")), str(x.get("name") or "").lower()))
        return {
            "account_name": chosen_account,
            "drive_type": drive_type,
            "dir_path": raw_dir_path,
            "exists": True,
            "pdir_fid": str(pdir_fid),
            "items": items,
            "paths": paths,
        }

