"""Directory traversal over an explicitly supplied share reader.

Account selection and transaction release belong to the application entrypoint.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, NamedTuple, Protocol
from app.domain.share_entries import (
    _bool_is_dir,
    _pick_name,
    _pick_fid,
    _pick_updated_at,
    _pick_size,
)


class ShareDirectoryAdapter(Protocol):
    def get_detail(self, pwd_id: str, stoken: str, pdir_fid: str) -> dict[str, Any]: ...


class ShareFileGroup(NamedTuple):
    files: list[dict[str, Any]]
    fid: str
    updated_at: Any
    name: str
    branch: tuple[str, str] | None
    priority: bool


@dataclass
class ShareListingReader:
    adapter: ShareDirectoryAdapter
    share_id: str
    token: str
    folder_id: str

    def files(
        self,
        max_depth: int = 3,
        folder_exclude: str = "",
        folder_exclude_mode: str = "",
    ) -> tuple[list[dict[str, Any]], bool, str]:
        adapter, pwd_id, stoken, extracted_pdir_fid = (
            self.adapter,
            self.share_id,
            self.token,
            self.folder_id,
        )
        all_files: list[dict[str, Any]] = []
        changed = False
        folder_exclude_keywords = (
            [kw.strip().lower() for kw in folder_exclude.split("|") if kw.strip()]
            if folder_exclude
            else []
        )
        folder_exclude_is_all = (
            str(folder_exclude_mode or "any").strip().lower() == "all"
        )

        def _collect_files(fid: str, depth: int, is_root: bool = True) -> None:
            nonlocal changed
            if depth > max_depth:
                return
            try:
                detail = adapter.get_detail(pwd_id, stoken, fid)
            except Exception:
                return
            raw_items = (((detail or {}).get("data") or {}).get("list")) or []
            if raw_items:
                changed = True
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                name = str(_pick_name(item) or "").strip()
                is_dir = bool(_bool_is_dir(item))
                if is_dir:
                    sub_fid = str(_pick_fid(item) or "").strip()
                    if not sub_fid or sub_fid == fid:
                        continue
                    name_lower = name.lower()
                    # 过滤：包含关键词的目录直接跳过，不进入
                    if folder_exclude_keywords:
                        if folder_exclude_is_all:
                            if all(kw in name_lower for kw in folder_exclude_keywords):
                                continue
                        else:
                            if any(kw in name_lower for kw in folder_exclude_keywords):
                                continue
                    # 递归进入子目录（无论是否匹配筛选词）
                    _collect_files(sub_fid, depth + 1, is_root=False)
                else:
                    all_files.append(
                        {
                            "file_name": name,
                            "is_dir": False,
                            "size": _pick_size(item),
                            "updated_at": _pick_updated_at(item),
                        }
                    )

        _collect_files(extracted_pdir_fid or "", 1, is_root=True)
        return all_files, changed, extracted_pdir_fid

    def groups(
        self,
        max_depth: int = 3,
        folder_filter: str = "",
        folder_exclude: str = "",
        folder_filter_mode: str = "",
        folder_exclude_mode: str = "",
        folder_priority: str = "",
        folder_priority_mode: str = "",
    ) -> list[ShareFileGroup]:
        folder_keywords = (
            [kw.strip().lower() for kw in folder_filter.split("|") if kw.strip()]
            if folder_filter
            else []
        )
        folder_exclude_keywords = (
            [kw.strip().lower() for kw in folder_exclude.split("|") if kw.strip()]
            if folder_exclude
            else []
        )
        folder_filter_is_any = str(folder_filter_mode or "all").strip().lower() == "any"
        folder_exclude_is_all = (
            str(folder_exclude_mode or "any").strip().lower() == "all"
        )
        folder_priority_keywords = (
            [kw.strip().lower() for kw in folder_priority.split("|") if kw.strip()]
            if folder_priority
            else []
        )
        folder_priority_is_any = (
            str(folder_priority_mode or "all").strip().lower() == "any"
        )

        def _is_priority_match(dir_name: str) -> bool:
            """判断目录名是否匹配优先级关键词"""
            if not folder_priority_keywords or not dir_name:
                return False
            name_lower = dir_name.lower()
            if folder_priority_is_any:
                return any(kw in name_lower for kw in folder_priority_keywords)
            else:
                return all(kw in name_lower for kw in folder_priority_keywords)

        listing_fid = str(self.folder_id or "")
        at_share_root = listing_fid in ("", "0", "/", "root")
        groups: list[ShareFileGroup] = []
        includes = any if folder_filter_is_any else all
        excludes = all if folder_exclude_is_all else any

        def _walk(
            fid: str,
            depth: int,
            dir_ts: Any = None,
            dir_name: str = "",
            branch: tuple[str, str] | None = None,
        ) -> None:
            if depth > max_depth:
                return
            try:
                detail = self.adapter.get_detail(self.share_id, self.token, fid)
            except Exception:
                return
            if not isinstance(detail, dict) or detail.get("code", 0) not in (0, "0"):
                return
            if detail.get("status", 200) not in (200, "200"):
                return
            data = detail.get("data")
            raw_items = data.get("list") if isinstance(data, dict) else None
            if not isinstance(raw_items, list):
                return
            metadata = detail.get("metadata") or {}
            if isinstance(metadata, dict) and (
                metadata.get("_listing_complete") is False
                or "_total" in metadata and str(metadata["_total"]) != str(len(raw_items))
            ):
                return
            local_files = []
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                name = str(_pick_name(item) or "").strip()
                if _bool_is_dir(item):
                    sub_fid = str(_pick_fid(item) or "").strip()
                    if not sub_fid or sub_fid == fid:
                        continue
                    if folder_exclude_keywords and excludes(kw in name.lower() for kw in folder_exclude_keywords):
                        continue
                    _walk(sub_fid, depth + 1, _pick_updated_at(item), name, branch or (sub_fid, name))
                else:
                    local_files.append({
                        "file_name": name,
                        "is_dir": False,
                        "size": _pick_size(item),
                        "updated_at": _pick_updated_at(item),
                    })
            if not local_files:
                return
            if folder_keywords and (dir_name or at_share_root):
                if not includes(kw in dir_name.lower() for kw in folder_keywords):
                    return
            selected_fid = "" if branch is None and at_share_root else fid
            groups.append(ShareFileGroup(local_files, selected_fid, dir_ts, dir_name, branch, _is_priority_match(dir_name)))

        _walk(listing_fid, 0 if at_share_root else 1)
        return sorted(groups, key=lambda group: group.priority, reverse=True)

    def root_dirs(self) -> list[tuple[str, str]]:
        adapter, pwd_id, stoken, extracted_pdir_fid = (
            self.adapter,
            self.share_id,
            self.token,
            self.folder_id,
        )
        fid = str(extracted_pdir_fid or "").strip()
        try:
            detail = adapter.get_detail(pwd_id, stoken, fid)
        except Exception:
            return []

        raw_items = (((detail or {}).get("data") or {}).get("list")) or []
        out: list[tuple[str, str]] = []
        has_root_files = False
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            if not _bool_is_dir(item):
                has_root_files = True
                continue
            sub_fid = str(_pick_fid(item) or "").strip()
            if not sub_fid:
                continue
            out.append((sub_fid, str(_pick_name(item) or "").strip()))
        # 根目录本身就有文件时不再下钻，否则会漏掉根目录的文件
        if has_root_files:
            return []
        return out
