"""网盘条目字段解析：把各适配器返回的原始 dict 归一成统一字段。

不同网盘对文件名/ID/大小/更新时间的命名差异很大（file_name、server_filename、fs_id、fid…），
这里集中做兼容解析。原先在 app/api/routes/tasks_common.py，P5 下沉到服务层。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import SupportsInt, SupportsIndex


def _integer(value: object) -> int:
    if not isinstance(value, (str, bytes, bytearray, SupportsInt, SupportsIndex)):
        raise TypeError("Invalid numeric directory metadata")
    return int(value)


def bool_is_dir(payload: Mapping[str, object]) -> bool:
    if payload.get("is_dir") is not None:
        return bool(payload.get("is_dir"))
    if payload.get("isdir") is not None:
        return str(payload.get("isdir")) in ("1", "true", "True")
    if payload.get("dir") is not None:
        return bool(payload.get("dir"))
    if payload.get("kind") in ("folder", "dir", "directory"):
        return True
    if payload.get("kind") in ("file",):
        return False
    if payload.get("type") in ("folder", "dir"):
        return True
    if payload.get("type") in ("file",):
        return False
    if payload.get("file_type") is not None:
        value = str(payload.get("file_type"))
        if value in ("0", "dir", "folder"):
            return True
        if value in ("1", "file"):
            return False
    return False


def pick_name(payload: Mapping[str, object]) -> str:
    return str(
        payload.get("file_name")
        or payload.get("server_filename")
        or payload.get("fileName")
        or payload.get("name")
        or payload.get("title")
        or payload.get("fid")
        or payload.get("fs_id")
        or ""
    )


def pick_fid(payload: Mapping[str, object]) -> str:
    return str(payload.get("fid") or payload.get("fs_id") or payload.get("file_id") or payload.get("id") or payload.get("fileId") or "")


def pick_fid_token(payload: Mapping[str, object]) -> str | None:
    value = payload.get("fid_token") or payload.get("share_fid_token") or payload.get("token")
    if value is None:
        return None
    return str(value)


def pick_updated_at(payload: Mapping[str, object]) -> object:
    return payload.get("updated_at") or payload.get("update_time") or payload.get("mtime") or payload.get("modified_at")


def pick_size(payload: Mapping[str, object]) -> int | None:
    if payload.get("size") is None:
        return None
    try:
        return _integer(payload.get("size"))
    except (TypeError, ValueError):
        return None


def pick_children_count(payload: Mapping[str, object]) -> int | None:
    if payload.get("include_items") is not None:
        try:
            return _integer(payload.get("include_items"))
        except (TypeError, ValueError):
            pass
    for key in ("children_count", "child_count", "child_cnt", "count", "cnt", "total"):
        if payload.get(key) is not None:
            try:
                return _integer(payload.get(key))
            except (TypeError, ValueError):
                pass
    file_count = None
    dir_count = None
    for key in ("file_count", "file_cnt", "files", "fileCount", "fileCnt", "sub_file_cnt", "subFileCount"):
        if payload.get(key) is not None:
            try:
                file_count = _integer(payload.get(key))
                break
            except (TypeError, ValueError):
                pass
    for key in ("dir_count", "dir_cnt", "dirs", "dirCount", "dirCnt", "sub_dir_cnt", "subDirCount"):
        if payload.get(key) is not None:
            try:
                dir_count = _integer(payload.get(key))
                break
            except (TypeError, ValueError):
                pass
    if file_count is None and dir_count is None:
        return None
    return int((file_count or 0) + (dir_count or 0))
