"""Audited, bounded, fail-closed listing for progress and Baidu transfer checks.

Legacy browsing methods intentionally retain their original pagination behavior.
No partial entries escape this module when a page is missing or ambiguous.
"""
from typing import Any
import time


def failure(message):
    return {"code": 1, "message": f"Complete directory listing failed: {message}", "data": {"list": []}}


def mapping(value):
    if not isinstance(value, dict):
        raise ValueError("Malformed page object")
    return value


def status(value, required=None, *, allow_http_code=False):
    mapping(value)
    if required and required not in value:
        raise ValueError(f"Missing page status: {required}")
    for key in ("code", "errno", "status", "errorCode"):
        accepted = ("0", "200") if key == "status" or allow_http_code else ("0",)
        if key in value and (isinstance(value[key], bool) or str(value[key]) not in accepted):
            raise ValueError(f"Page error {key}={value[key]}")
    if required in ("code", "errno") and str(value[required]) != "0":
        raise ValueError(f"Page error {required}={value[required]}")
    for key in ("success", "ok", "state"):
        if key in value and value[key] not in (True, 1):
            raise ValueError(f"Page error {key}")


def rows(value):
    if not isinstance(value, list) or any(not isinstance(i, dict) for i in value):
        raise ValueError("Malformed directory item list")
    return value


def integer(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).isdigit():
        raise ValueError("Malformed directory total")
    return int(value)


def validate_entries(items):
    seen = set()
    for item in rows(items):
        fid = next((item[k] for k in ("fid", "file_id", "fileId", "FileId", "fs_id", "id", "path", "resId", "cid") if k in item), None)
        name = next((item[k] for k in ("file_name", "name", "fileName", "FileName", "server_filename", "title", "n") if k in item), None)
        if name is None and isinstance(item.get("path"), str):
            name = item["path"].rsplit("/", 1)[-1]
        if isinstance(fid, bool) or not isinstance(fid, (str, int)) or not str(fid).strip():
            raise ValueError("Missing or malformed directory item ID")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Missing or malformed directory item name")
        key = str(fid).strip()
        if key in seen:
            raise ValueError("Duplicate directory item ID (repeated/overlapping page)")
        seen.add(key)


def validate_result(result):
    mapping(result)
    if str(result.get("code")) != "0":
        raise ValueError(result.get("message", "Directory listing error"))
    status(result, "code")
    items = rows(mapping(result.get("data")).get("list"))
    validate_entries(items)
    metadata = result.get("metadata", {})
    mapping(metadata)
    for key in ("_total", "total", "expected_total"):
        if key in metadata and integer(metadata[key]) != len(items):
            raise ValueError("Directory total does not match enumerated entries")
    for source in (result, result["data"]):
        for key in ("total", "expected_total"):
            if key in source and integer(source[key]) != len(items):
                raise ValueError("Directory total does not match enumerated entries")
    return result


class Collector:
    def __init__(self):
        self.items = []
        self.expected = None
        self.cursors = set()
        self.ids = set()

    def add(self, batch, total=None):
        rows(batch)
        if total is not None:
            total = integer(total)
            if self.expected is not None and self.expected != total:
                raise ValueError("Directory total changed while enumerating")
            self.expected = total
        validate_entries(batch)
        for item in batch:
            fid = str(item["fid"]).strip()
            if fid in self.ids:
                raise ValueError("Duplicate directory item ID (repeated/overlapping page)")
            self.ids.add(fid)
        self.items.extend(batch)
        if self.expected is not None and len(self.items) > self.expected:
            raise ValueError("Directory total is smaller than enumerated entries")

    def cursor(self, value):
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise ValueError("Malformed next-page cursor")
        key = str(value)
        if key in self.cursors:
            raise ValueError("Repeated next-page cursor")
        self.cursors.add(key)

    def result(self):
        if self.expected is not None and self.expected != len(self.items):
            raise ValueError("Directory ended before expected total")
        return {"code": 0, "message": "success", "data": {"list": self.items}, "metadata": {"_total": len(self.items)}}


def complete_listing(adapter, parent):
    try:
        return _enumerate(adapter, parent)
    except Exception as exc:
        return failure(str(exc))


def _cloud189_portal_root(a):
    """The local portal API has no verified page request controls.

    Accept only a response whose advertised total proves the entire root was
    returned. Never attempt to infer or guess a second-page request contract.
    """
    raw = a._request_json("GET", f"{a.HOST_URL}/api/portal/listFiles.action", params={"fileId": "-11", "noCache": str(time.time())}, timeout=15)
    status(raw)
    source = rows(raw.get("data"))
    validate_entries(source)
    total = integer(raw.get("recordCount"))
    if total != len(source):
        raise ValueError("189 portal root response is incomplete; root pagination is unsupported")
    if "pageNum" in raw and integer(raw["pageNum"]) != 1:
        raise ValueError("189 portal root response is not the first page")
    if "pageSize" in raw:
        size = integer(raw["pageSize"])
        if size < len(source) or size == 0:
            raise ValueError("189 portal root page size is inconsistent")
    c = Collector()
    c.add([{"fid": str(i["fileId"]), "file_name": i["fileName"], "dir": bool(i.get("isFolder")), "size": i.get("fileSize", 0)} for i in source], total)
    return c.result()


def _enumerate(a, parent):
    kind = a.DRIVE_TYPE
    c = Collector()
    cap = integer(a.COMPLETE_LISTING_MAX_PAGES)
    cursor: Any = ""
    if kind == "baidu":
        if not a._session:
            raise ValueError("Baidu client unavailable")
        # The legacy numeric-ID resolver searches only first pages and silently
        # falls back to root. It cannot establish the requested directory.
        parent = str(parent or "0")
        if parent == "0":
            parent = "/"
        elif not parent.startswith("/"):
            raise ValueError("Baidu complete listing requires an absolute directory path")
    elif kind == "aliyun":
        if not a._ensure_token_valid() or not a._token or not a._token.default_drive_id:
            raise ValueError("Aliyun token or drive unavailable")
    elif kind == "xunlei":
        if not a._ensure_tokens_valid():
            raise ValueError("Xunlei token unavailable")
    elif kind == "123pan":
        if not a._ensure_login():
            raise ValueError("123pan login failed")
        parent = int(parent or 0)
    elif kind in ("cloud189", "guangya"):
        a._ensure_login()
    if kind == "cloud189" and str(a._map_root_fid(parent)) == "-11":
        return _cloud189_portal_root(a)
    for index in range(cap):
        page = index + 1
        total = None
        next_cursor = None
        terminal = False
        if kind in ("quark", "uc"):
            params = {"pr": "ucpro" if kind == "quark" else "UCBrowser", "fr": "pc", "pdir_fid": parent or "0", "_page": page, "_size": "50", "_fetch_total": "1", "_fetch_sub_dirs": "0", "_sort": "file_type:asc,updated_at:desc"}
            if kind == "quark":
                params.update({"uc_param_str": "", "fetch_all_file": 1, "fetch_risk_file_name": 1})
            response = a._send_request("GET", f"{a.BASE_URL}/1/clouddrive/file/sort", params=params)
            raw = response.json() if kind == "quark" else a._safe_json(response)
            status(raw, "code")
            batch = rows(mapping(raw.get("data")).get("list"))
            meta = mapping(raw.get("metadata"))
            total = integer(meta.get("_total"))
            terminal = len(c.items) + len(batch) >= total
        elif kind == "baidu":
            raw = a._api_list_page(parent, page)
            status(raw, "errno")
            source = rows(raw.get("list"))
            validate_entries(source)
            batch = [{"fid": i["path"], "file_name": i["path"].rsplit("/", 1)[-1], "dir": i.get("isdir") == 1, "size": i.get("size", 0), "updated_at": i.get("server_mtime", 0)} for i in source]
            total = raw.get("total")
            terminal = len(source) < a._API_LIST_PAGE_SIZE
            if "has_more" in raw:
                if raw["has_more"] not in (True, False, 0, 1):
                    raise ValueError("Malformed Baidu has_more flag")
                terminal = not raw["has_more"]
        elif kind == "123pan":
            params = {"driveId": "0", "limit": "100", "next": cursor or "0", "orderBy": "file_id", "orderDirection": "desc", "parentFileId": str(parent), "trashed": "false", "SearchData": "", "Page": str(page), "OnlyLookAbnormalFile": "0"}
            raw = a._request("GET", "/b/api/file/list/new", params=params, timeout=30)
            status(raw, "code")
            data = mapping(raw.get("data"))
            source = rows(data.get("InfoList"))
            validate_entries(source)
            batch = [a._convert_dir_item(i) for i in source]
            if "Next" not in data or data["Next"] is None:
                raise ValueError("123pan did not supply an unambiguous Next cursor")
            next_cursor = data["Next"]
            terminal = next_cursor in (-1, "-1")
            total = data.get("Total")
        elif kind == "aliyun":
            from .aliyun_adapter import ADRIVE_V3_FILE_LIST
            body = {"drive_id": a._token.default_drive_id, "parent_file_id": str(parent) if parent and str(parent) != "0" else "root", "limit": 100, "marker": cursor, "order_by": "name", "order_direction": "ASC"}
            raw = a._check_response(a._request("POST", ADRIVE_V3_FILE_LIST, body=body))
            status(raw)
            source = rows(raw.get("items"))
            validate_entries(source)
            batch = [a._convert_item(i) for i in source]
            if "next_marker" not in raw or not isinstance(raw["next_marker"], str):
                raise ValueError("Missing or malformed Aliyun next_marker")
            next_cursor = raw["next_marker"]
            terminal = next_cursor == ""
        elif kind == "xunlei":
            from .xunlei_adapter import API_BASE, FILE_LIST_FILTERS
            params = {"filters": FILE_LIST_FILTERS, "with_audit": "true", "thumbnail_size": "SIZE_SMALL", "limit": 100}
            if parent and str(parent) != "0":
                params["parent_id"] = str(parent)
            if cursor:
                params["page_token"] = cursor
            raw = a._request("GET", f"{API_BASE}/drive/v1/files", params=params)
            status(raw)
            if a._has_error(raw):
                raise ValueError(a._get_error_message(raw))
            source = rows(raw.get("files"))
            validate_entries(source)
            batch = [a._convert_xunlei_item(i) for i in source]
            if "next_page_token" not in raw or not isinstance(raw["next_page_token"], str):
                raise ValueError("Missing or malformed Xunlei next_page_token")
            next_cursor = raw["next_page_token"]
            terminal = next_cursor == ""
        elif kind == "115":
            params = {"aid": 1, "cid": parent or "0", "o": "user_ptime", "asc": 1, "offset": index * 50, "show_dir": 1, "limit": 50, "type": 0, "format": "json", "fc_mix": 0}
            raw = a._safe_json(a._request(a.auth_session, "GET", f"{a.API_URL}/files", params=params, timeout=15))
            status(raw, "state")
            source = rows(raw.get("data"))
            # 115 uses fid/n for files and cid/n for folders.
            source_ids = [{"fid": i.get("fid", i.get("cid")), "file_name": i.get("n")} for i in source]
            validate_entries(source_ids)
            batch = [a._convert_dir_item(i) for i in source]
            total = raw.get("count")
            terminal = len(source) < 50 if total is None else len(c.items) + len(batch) >= integer(total)
        elif kind == "cloud139":
            folder = "/" if str(parent or "") in ("", "0", "/", "root") else str(parent)
            raw = a._list_disk_dir(folder, cursor=cursor or None)
            status(raw)
            source = rows(raw.get("items", raw.get("fileList")))
            validate_entries(source)
            batch = [{"fid": str(i.get("fileId") or i.get("id")), "file_name": i.get("name", i.get("fileName")), "dir": str(i.get("fileType") or i.get("category") or "").lower() == "folder", "size": i.get("size", 0)} for i in source]
            info = mapping(raw.get("pageInfo", {}))
            if "nextPageCursor" not in raw and "nextPageCursor" not in info:
                raise ValueError("Missing 139 nextPageCursor")
            next_cursor = raw.get("nextPageCursor", info.get("nextPageCursor"))
            terminal = next_cursor in (None, "")
            total = raw.get("total", info.get("total"))
        elif kind == "cloud189":
            fid = a._map_root_fid(parent)
            raw = a._request_json("GET", f"{a.HOST_URL}/api/open/file/listFiles.action", params={"folderId": str(fid), "orderBy": "lastOpTime", "descending": "true", "pageNum": page, "pageSize": 60, "iconOption": 5, "mediaType": 0}, timeout=15)
            status(raw)
            data = mapping(raw.get("fileListAO"))
            source = rows(data.get("folderList")) + rows(data.get("fileList"))
            validate_entries(source)
            batch = [{"fid": str(i["id"]), "file_name": i["name"], "dir": "fileCount" in i or bool(i.get("isFolder")), "size": i.get("size", 0)} for i in source]
            total = integer(data.get("count"))
            terminal = len(c.items) + len(batch) >= total
        elif kind == "guangya":
            from .guangya_adapter import _is_success
            raw = a._client.fs_files(parent_id=a._normalize_root_parent_id(parent), page=index, page_size=50)
            status(raw, allow_http_code=True)
            if not _is_success(raw):
                raise ValueError("Guangya listing failed")
            data = raw.get("data", raw)
            if isinstance(data, list):
                source = rows(data)
            else:
                data = mapping(data)
                key = next((k for k in ("list", "items", "records", "rows", "fileList", "files") if k in data), None)
                if key is None:
                    raise ValueError("Missing Guangya directory list")
                source = rows(data[key])
                total = data.get("total")
            validate_entries(source)
            batch = [{"fid": a._pick_item_id(i), "file_name": a._pick_name(i), "dir": a._pick_is_dir(i), "size": a._pick_size(i)} for i in source]
            terminal = len(source) < 50 if total is None else len(c.items) + len(batch) >= integer(total)
            if isinstance(data, dict) and data.get("hasMore") is True:
                terminal = False
            if isinstance(data, dict) and "hasMore" in data:
                if not isinstance(data["hasMore"], bool):
                    raise ValueError("Malformed Guangya hasMore flag")
                if data["hasMore"] is False:
                    terminal = True
        else:
            raise ValueError(f"Complete listing is unsupported: {kind}")
        c.add(batch, total)
        if terminal:
            return c.result()
        if not batch:
            raise ValueError("Empty page before directory completion")
        if next_cursor is not None:
            c.cursor(next_cursor)
            cursor = next_cursor
    raise ValueError("Directory pagination safety limit reached before completion")
