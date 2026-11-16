"""Directory traversal over an explicitly supplied share reader.

Account selection and transaction release belong to the application entrypoint.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Protocol
from app.domain.share_entries import (
    _bool_is_dir,
    _pick_name,
    _pick_fid,
    _pick_updated_at,
    _pick_size,
)


class ShareDirectoryAdapter(Protocol):
    def get_detail(self, pwd_id: str, stoken: str, pdir_fid: str) -> dict[str, Any]: ...


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
    ) -> list[tuple[list[dict[str, Any]], str, Any, str]]:
        adapter, pwd_id, stoken, extracted_pdir_fid = (
            self.adapter,
            self.share_id,
            self.token,
            self.folder_id,
        )
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

        def _collect_from_dir(
            fid: str, dir_ts: Any = None, dir_name: str = ""
        ) -> list[tuple[list[dict[str, Any]], str, Any, str]]:
            """收集目录下的文件，按层级分组返回。
            当目录同时有文件和子目录时，文件单独一组，子目录各自递归成组。
            不匹配筛选词的目录仍会递归进入，但不收集该层的文件。"""

            def _walk(
                f: str, depth: int, f_ts: Any, f_name: str = "", is_root: bool = True
            ) -> list[tuple[list[dict[str, Any]], str, Any, str]]:
                groups: list[tuple[list[dict[str, Any]], str, Any, str]] = []
                if depth > max_depth:
                    return groups
                try:
                    detail = adapter.get_detail(pwd_id, stoken, f)
                except Exception:
                    return groups
                raw_items = (((detail or {}).get("data") or {}).get("list")) or []
                local_files: list[dict[str, Any]] = []
                # 判断当前目录是否匹配筛选词
                collect_files = True
                if folder_keywords and f_name:
                    name_lower = f_name.lower()
                    if folder_filter_is_any:
                        collect_files = any(kw in name_lower for kw in folder_keywords)
                    else:
                        collect_files = all(kw in name_lower for kw in folder_keywords)
                for item in raw_items:
                    if not isinstance(item, dict):
                        continue
                    name = str(_pick_name(item) or "").strip()
                    is_dir = bool(_bool_is_dir(item))
                    if is_dir:
                        sub_fid = str(_pick_fid(item) or "").strip()
                        if not sub_fid or sub_fid == f:
                            continue
                        name_lower = name.lower()
                        # 过滤：包含关键词的目录直接跳过，不进入
                        if folder_exclude_keywords:
                            if folder_exclude_is_all:
                                if all(
                                    kw in name_lower for kw in folder_exclude_keywords
                                ):
                                    continue
                            else:
                                if any(
                                    kw in name_lower for kw in folder_exclude_keywords
                                ):
                                    continue
                        # 递归进入子目录（无论是否匹配筛选词）
                        groups.extend(
                            _walk(
                                sub_fid,
                                depth + 1,
                                _pick_updated_at(item),
                                f_name=name,
                                is_root=False,
                            )
                        )
                    else:
                        local_files.append(
                            {
                                "file_name": name,
                                "is_dir": False,
                                "size": _pick_size(item),
                                "updated_at": _pick_updated_at(item),
                            }
                        )
                if local_files and collect_files:
                    groups.append((local_files, f, f_ts, f_name))
                return groups

            return _walk(fid, 1, dir_ts, f_name=dir_name, is_root=True)

        def _sort_by_priority(
            groups: list[tuple[list[dict[str, Any]], str, Any, str]],
        ) -> list[tuple[list[dict[str, Any]], str, Any, str]]:
            """按优先级排序：匹配关键词的文件夹排前面"""
            if not folder_priority_keywords:
                return groups

            priority_groups = []
            normal_groups = []
            for g in groups:
                # g 是 (files, fid, ts, dir_name) 的元组
                dir_name = g[3] if len(g) > 3 else ""
                if _is_priority_match(dir_name):
                    priority_groups.append(g)
                else:
                    normal_groups.append(g)

            # 优先级组排前面，保持各自内部顺序
            return priority_groups + normal_groups

        # 有 fid 的情况：跟原逻辑一样，返回一组
        if extracted_pdir_fid:
            selected = _collect_from_dir(extracted_pdir_fid)
            if selected:
                return _sort_by_priority(selected)
            return []

        # 无 fid 的情况：按根目录下的子目录分组
        try:
            detail = adapter.get_detail(pwd_id, stoken, "")
        except Exception:
            return []

        raw_items = (((detail or {}).get("data") or {}).get("list")) or []
        groups: list[tuple[list[dict[str, Any]], str, Any, str]] = []

        for item in raw_items:
            if not isinstance(item, dict):
                continue
            is_dir = bool(_bool_is_dir(item))
            if is_dir:
                sub_fid = str(_pick_fid(item) or "").strip()
                if not sub_fid:
                    continue
                sub_name = str(_pick_name(item) or "").strip()
                sub_groups = _collect_from_dir(
                    sub_fid, _pick_updated_at(item), dir_name=sub_name
                )
                for g in sub_groups:
                    if g[0]:
                        groups.append(g)

        return _sort_by_priority(groups)

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
