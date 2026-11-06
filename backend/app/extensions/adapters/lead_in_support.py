# -*- coding: utf-8 -*-
"""
任务引流（引流文件）适配器共用逻辑

夸克、百度等网盘适配器共用这里的结果构造、分享项归一化、同一文件判定、
目标目录枚举与有界核验逻辑；各网盘私有的差异（分享令牌、目录定位、
转存提交、任务状态查询）仍留在各自适配器里实现。
"""
import time
from typing import Any, Dict, List, Optional, Tuple


# ==================== 状态与错误码 ====================
STATE_ABSENT = "absent"
STATE_EXISTS = "exists"
STATE_CONFLICT = "conflict"
STATE_ADDED = "added"
STATE_PENDING = "pending"
STATE_FAILED = "failed"

# 核验轮询的默认上限（次数与间隔），保证查询总时长有界
DEFAULT_VERIFY_ATTEMPTS = 15
DEFAULT_VERIFY_INTERVAL = 1.0


class DramaLeadInMixin:
    """任务引流能力的共用实现，与 BaseCloudDriveAdapter 一起混入使用。"""

    # 核验写入结果时的有界轮询参数（子类可覆盖）
    LEAD_IN_MAX_ATTEMPTS = DEFAULT_VERIFY_ATTEMPTS
    LEAD_IN_POLL_INTERVAL = DEFAULT_VERIFY_INTERVAL

    # ==================== 结果构造 ====================
    def _lead_in_ok(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """引流方法成功返回"""
        return {"code": 0, "message": "", "data": data}

    def _lead_in_error(
        self, code: str, message: str, data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """引流方法失败返回，统一使用中文原因；必要时可携带供前端选择的列表数据"""
        return {"code": code, "message": message, "data": data}

    # ==================== 分享项归一化 ====================
    @staticmethod
    def _lead_in_to_int(value: Any, default: int = 0) -> int:
        """尽力把网盘返回值转成整数"""
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _lead_in_item_is_dir(item: Dict[str, Any]) -> bool:
        """判断分享/目录条目是否为文件夹"""
        if not isinstance(item, dict):
            return False
        for key in ("dir", "is_dir", "isdir"):
            if item.get(key) is not None:
                value = item.get(key)
                if isinstance(value, str):
                    return value.strip().lower() in ("1", "true", "yes")
                return bool(value)
        file_type = item.get("file_type")
        if file_type is not None:
            # 夸克/百度约定 file_type：0=目录，1=文件
            return str(file_type) in ("0", "dir", "folder")
        kind = item.get("kind") or item.get("type")
        if kind:
            return str(kind).lower() in ("dir", "folder", "directory")
        return False

    def _lead_in_entry(self, item: Dict[str, Any], base_path: str = "") -> Dict[str, Any]:
        """把不同网盘的分享项/目录项归一成内部条目"""
        item = item if isinstance(item, dict) else {}
        fid = str(item.get("fid") or item.get("fs_id") or item.get("file_id") or item.get("id") or "").strip()
        path = str(item.get("path") or "").strip()
        name = str(
            item.get("file_name")
            or item.get("server_filename")
            or item.get("name")
            or item.get("title")
            or ""
        ).strip()
        if not name and path:
            name = path.rstrip("/").rsplit("/", 1)[-1]
        if not path and name:
            base = base_path.rstrip("/")
            path = f"{base}/{name}" if base else f"/{name}"
        token = item.get("share_fid_token") or item.get("fid_token") or item.get("token")
        md5 = item.get("md5") or item.get("content_md5") or item.get("hash")
        return {
            "fid": fid,
            "name": name,
            "size": max(0, self._lead_in_to_int(item.get("size") or item.get("file_size") or item.get("bytes"))),
            "dir": self._lead_in_item_is_dir(item),
            "path": path,
            "md5": str(md5 or "").strip().lower(),
            "token": str(token).strip() if token is not None else "",
        }

    @staticmethod
    def _lead_in_public_files(entries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """对外展示的文件列表条目（含 path，便于前端展示分享内路径）"""
        return [
            {
                "fid": entry.get("fid", ""),
                "name": entry.get("name", ""),
                "size": entry.get("size", 0),
                "dir": bool(entry.get("dir")),
                "path": entry.get("path", ""),
            }
            for entry in entries
        ]

    @staticmethod
    def _lead_in_public_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
        """目录条目对外字段"""
        return {
            "fid": entry.get("fid", ""),
            "name": entry.get("name", ""),
            "size": entry.get("size", 0),
            "dir": bool(entry.get("dir")),
        }

    def _lead_in_build_material(
        self,
        entry: Dict[str, Any],
        pwd_id: str = "",
        stoken: str = "",
        shareurl: str = "",
        passcode: str = "",
    ) -> Dict[str, Any]:
        """构造素材描述，供查重、写入、核验阶段原样透传"""
        return {
            "fid": entry.get("fid", ""),
            "name": entry.get("name", ""),
            "size": entry.get("size", 0),
            "path": entry.get("path", ""),
            "material_token": entry.get("token") or entry.get("fid", ""),
            "md5": entry.get("md5", ""),
            "pwd_id": pwd_id or "",
            "stoken": stoken or "",
            "shareurl": shareurl or "",
            "passcode": passcode or "",
        }

    # ==================== 本账号文件素材 ====================
    def _lead_in_normalize_own_path(
        self, file_path: str
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        """归一「本账号文件」的完整路径，拒绝根路径/URL/上级目录"""
        raw = str(file_path or "").strip().replace("\\", "/")
        if not raw:
            return "", self._lead_in_error("LEAD_IN_DIR_INVALID", "请填写账号内文件的完整路径")
        if "://" in raw:
            return "", self._lead_in_error(
                "LEAD_IN_DIR_INVALID", "请填写账号内文件的完整路径，而不是链接地址"
            )
        if not raw.startswith("/"):
            return "", self._lead_in_error(
                "LEAD_IN_DIR_INVALID", "请填写账号内文件的完整路径（需要以 / 开头）"
            )
        parts: List[str] = []
        for part in raw.split("/"):
            text = part.strip()
            if not text or text == ".":
                continue
            if text == "..":
                return "", self._lead_in_error(
                    "LEAD_IN_DIR_INVALID", "文件路径不能包含上级目录（..）"
                )
            parts.append(text)
        if not parts:
            return "", self._lead_in_error(
                "LEAD_IN_DIR_INVALID", "请填写账号内文件的完整路径，不能使用网盘根目录"
            )
        return "/" + "/".join(parts), None

    def _lead_in_build_own_material(
        self,
        entry: Dict[str, Any],
        path: str,
        account_name: str = "",
    ) -> Dict[str, Any]:
        """构造「本账号文件」素材描述，供查重、复制、核验阶段透传"""
        entry = entry if isinstance(entry, dict) else {}
        normalized, _error = self._lead_in_normalize_own_path(path)
        material_path = normalized or str(path or "").strip()
        name = str(entry.get("name") or "").strip() or material_path.rsplit("/", 1)[-1]
        md5 = entry.get("md5") or ""
        return {
            "source": "own_file",
            "fid": str(entry.get("fid") or "").strip(),
            "name": name,
            "path": material_path,
            "size": max(0, self._lead_in_to_int(entry.get("size"))),
            "md5": str(md5).strip().lower(),
            "dir": False,
            "account_name": str(account_name or "").strip(),
        }

    # ==================== 本账号目录浏览 ====================
    def _lead_in_normalize_own_dir_path(
        self, dir_path: str
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        """归一化「本账号文件」浏览目录路径，根目录记为 /"""
        raw = str(dir_path or "").strip().replace("\\", "/")
        if not raw:
            return "", self._lead_in_error("LEAD_IN_DIR_INVALID", "目录路径不合法")
        if "://" in raw:
            return "", self._lead_in_error(
                "LEAD_IN_DIR_INVALID", "目录路径不合法，不能填写链接地址"
            )
        if not raw.startswith("/"):
            return "", self._lead_in_error(
                "LEAD_IN_DIR_INVALID", "目录路径不合法，需要以 / 开头"
            )
        parts: List[str] = []
        for part in raw.split("/"):
            if not part:
                # 重复的 / 视为分隔符，直接合并
                continue
            text = part.strip()
            if not text:
                return "", self._lead_in_error(
                    "LEAD_IN_DIR_INVALID", "目录路径不合法，不能包含空路径段"
                )
            if text in (".", ".."):
                return "", self._lead_in_error(
                    "LEAD_IN_DIR_INVALID", "目录路径不合法，不能包含 . 或 .."
                )
            parts.append(text)
        if not parts:
            return "/", None
        return "/" + "/".join(parts), None

    def _lead_in_locate_own_dir_fid(
        self, path: str
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        """按完整路径定位账号内目录 fid；根目录固定为 0"""
        if path == "/":
            return "0", None
        try:
            matches = self.get_fids([path])
        except Exception as exc:  # noqa: BLE001 - 网络层异常统一映射为引流错误码
            return "", self._lead_in_dir_error(exc)
        if isinstance(matches, dict):
            for key in ("data", "list"):
                value = matches.get(key)
                if isinstance(value, list):
                    matches = value
                    break
            else:
                matches = []
        if not isinstance(matches, list):
            matches = []
        target = path.rstrip("/")
        for item in matches:
            if not isinstance(item, dict):
                continue
            item_path = (
                str(item.get("file_path") or item.get("path") or "")
                .strip()
                .replace("\\", "/")
            )
            if item_path.rstrip("/") != target:
                continue
            fid = str(item.get("fid") or "").strip()
            if fid:
                return fid, None
            break
        return "", self._lead_in_error(
            "LEAD_IN_DIR_NOT_FOUND", "目录不存在，请重新选择"
        )

    @staticmethod
    def _lead_in_dir_error_from_message(message: Any) -> Dict[str, Any]:
        """把列目录失败信息映射成引流错误码（超时与一般失败区分）"""
        text = str(message or "").strip()
        if any(
            word in text.lower()
            for word in ("超时", "timeout", "timed out", "timed-out")
        ):
            return {
                "code": "LEAD_IN_TIMEOUT",
                "message": "读取目录超时，请稍后重试",
                "data": None,
            }
        return {
            "code": "LEAD_IN_DIR_LIST_FAILED",
            "message": "读取目录失败，请稍后重试",
            "data": None,
        }

    def _lead_in_dir_error(self, exc: Exception) -> Dict[str, Any]:
        if isinstance(exc, TimeoutError):
            return {
                "code": "LEAD_IN_TIMEOUT",
                "message": "读取目录超时，请稍后重试",
                "data": None,
            }
        return self._lead_in_dir_error_from_message(exc)

    def lead_in_list_own_dir(self, dir_path: str) -> Dict[str, Any]:
        """浏览当前账号内的目录（只读），夸克/百度共用"""
        path, error = self._lead_in_normalize_own_dir_path(dir_path)
        if error:
            return error
        fid, error = self._lead_in_locate_own_dir_fid(path)
        if error:
            return error
        try:
            listing = self.ls_dir(fid, max_items=200)
        except Exception as exc:  # noqa: BLE001
            return self._lead_in_dir_error(exc)
        if not isinstance(listing, dict):
            return self._lead_in_dir_error_from_message("")
        code = listing.get("code")
        if code not in (0, "0", None):
            return self._lead_in_dir_error_from_message(listing.get("message"))
        data = listing.get("data")
        raw_items = data.get("list") if isinstance(data, dict) else None
        if not isinstance(raw_items, list):
            return self._lead_in_dir_error_from_message(listing.get("message"))
        base_path = "" if path == "/" else path
        entries: List[Dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            # 名称保留网盘返回的原值（仅用去空白后的结果判断是否为空）
            name = str(item.get("file_name") or item.get("name") or "")
            if not name.strip():
                continue
            is_dir = self._lead_in_item_is_dir(item)
            entry_path = f"{base_path}/{name}" if base_path else f"/{name}"
            entry_fid = str(
                item.get("fid") or item.get("fs_id") or item.get("file_id") or ""
            ).strip()
            entries.append(
                {
                    "fid": entry_fid or entry_path,
                    "name": name,
                    "dir": is_dir,
                    "size": 0
                    if is_dir
                    else max(0, self._lead_in_to_int(item.get("size"))),
                    "path": entry_path,
                }
            )
            if len(entries) >= 200:
                break
        entries.sort(
            key=lambda entry: (0 if entry["dir"] else 1, entry["name"].lower())
        )
        return self._lead_in_ok({"path": path, "fid": fid, "entries": entries})

    # ==================== 内部复制错误映射 ====================
    @staticmethod
    def _lead_in_copy_error(
        message: str = "",
        code: Any = None,
        conflict_codes: Tuple[Any, ...] = (),
        unavailable_codes: Tuple[Any, ...] = (),
        timeout_codes: Tuple[Any, ...] = (),
    ) -> Optional[Dict[str, Any]]:
        """把网盘内部复制的失败结果映射成引流错误码；无法判断时返回 None 交给调用方"""
        text = str(message or "").strip()
        lowered = text.lower()
        if code in conflict_codes or any(
            word in text for word in ("已存在", "同名", "重复", "冲突")
        ) or any(word in lowered for word in ("exists", "duplicate", "conflict")):
            return {
                "code": "LEAD_IN_NAME_CONFLICT",
                "message": "目标目录存在同名文件，未做任何覆盖" + (f"（{text}）" if text else ""),
                "data": None,
            }
        if code in timeout_codes or any(
            word in lowered for word in ("超时", "timeout", "timed out", "timed-out")
        ):
            return {
                "code": "LEAD_IN_TIMEOUT",
                "message": "复制请求超时，添加结果待确认" + (f"（{text}）" if text else ""),
                "data": None,
            }
        if code in unavailable_codes or any(
            word in text for word in ("不存在", "已删除", "失效", "无权限", "找不到")
        ):
            return {
                "code": "LEAD_IN_MATERIAL_UNAVAILABLE",
                "message": "素材文件不存在或无权访问：请检查路径" + (f"（{text}）" if text else ""),
                "data": None,
            }
        return None

    # ==================== 素材选择 ====================
    def _lead_in_pick_entry(
        self,
        entries: List[Dict[str, Any]],
        selected_fid: str = "",
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """从分享条目中定位唯一素材文件，返回 (条目, 错误)"""
        selected = str(selected_fid or "").strip()
        files = [entry for entry in entries if not entry.get("dir")]
        if selected:
            picked = next((entry for entry in entries if entry.get("fid") == selected), None)
            if picked is None:
                return None, self._lead_in_error(
                    "LEAD_IN_MATERIAL_UNAVAILABLE",
                    "选中的素材文件已不在分享内，请重新读取分享文件列表",
                )
            if picked.get("dir"):
                return None, self._lead_in_error(
                    "LEAD_IN_MATERIAL_UNAVAILABLE",
                    "选中的是文件夹，引流素材必须是单个文件（禁止整目录转存）",
                )
            return picked, None
        if not files:
            return None, self._lead_in_error(
                "LEAD_IN_MATERIAL_UNAVAILABLE",
                "分享内没有可用的素材文件（仅文件夹不能作为引流素材）",
            )
        if len(entries) == 1:
            # 分享根下只有一个文件时自动选中
            return files[0], None
        return None, self._lead_in_error(
            "LEAD_IN_MATERIAL_AMBIGUOUS",
            "分享内含多个文件或文件夹，请先读取分享文件列表并选择要使用的素材文件",
        )

    # ==================== 目录处理 ====================
    def _lead_in_normalize_dir_path(self, dir_path: str) -> Tuple[str, Optional[Dict[str, Any]]]:
        """归一保存目录路径，拒绝根目录/非法路径"""
        raw = str(dir_path or "").strip().replace("\\", "/")
        if not raw:
            return "", self._lead_in_error("LEAD_IN_DIR_INVALID", "请先选择任务保存目录")
        if "://" in raw:
            return "", self._lead_in_error("LEAD_IN_DIR_INVALID", "保存目录不是网盘目录路径")
        parts: List[str] = []
        for part in raw.split("/"):
            text = part.strip()
            if not text or text == ".":
                continue
            if text == "..":
                return "", self._lead_in_error("LEAD_IN_DIR_INVALID", "保存目录不能包含上级路径")
            parts.append(text)
        if not parts:
            return "", self._lead_in_error("LEAD_IN_DIR_INVALID", "不能使用网盘根目录作为保存目录")
        return "/" + "/".join(parts), None

    def _lead_in_target_dir(self, target: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """取出目标目录信息，返回 (目录, 错误)"""
        if not isinstance(target, dict):
            return {}, self._lead_in_error("LEAD_IN_FAILED", "目标目录信息缺失，请重新选择保存目录")
        dir_info = target.get("dir")
        if not isinstance(dir_info, dict) or not str(dir_info.get("fid") or "").strip():
            return {}, self._lead_in_error("LEAD_IN_FAILED", "目标目录信息缺失，请重新选择保存目录")
        return dir_info, None

    def _lead_in_fetch_target_entries(
        self, target: Dict[str, Any]
    ) -> Tuple[Optional[List[Dict[str, Any]]], Optional[Dict[str, Any]]]:
        """枚举目标目录最新条目；读取失败时回退调用方传入的目录快照"""
        dir_info, error = self._lead_in_target_dir(target)
        if error:
            return None, error
        dir_fid = str(dir_info.get("fid") or "").strip()
        try:
            listing = self.ls_dir(dir_fid, max_items=0)
        except Exception:
            listing = None
        raw_items = None
        if isinstance(listing, dict):
            data = listing.get("data")
            if isinstance(data, dict) and isinstance(data.get("list"), list):
                raw_items = data.get("list")
        if isinstance(raw_items, list):
            return [self._lead_in_entry(item) for item in raw_items if isinstance(item, dict)], None
        snapshot = target.get("entries") if isinstance(target, dict) else None
        if isinstance(snapshot, list):
            return [self._lead_in_entry(item) for item in snapshot if isinstance(item, dict)], None
        message = ""
        if isinstance(listing, dict):
            message = str(listing.get("message") or "").strip()
        return None, self._lead_in_error(
            "LEAD_IN_FAILED",
            "读取目标目录失败" + (f"：{message}" if message else ""),
        )

    # ==================== 同一文件判定 ====================
    def _lead_in_is_same_file(self, material: Dict[str, Any], entry: Dict[str, Any]) -> bool:
        """判定目标目录条目是否就是素材文件本身（按网盘文件标识或可靠属性）"""
        if not isinstance(material, dict) or not isinstance(entry, dict):
            return False
        if entry.get("dir"):
            return False
        name_a = str(material.get("name") or "").strip()
        name_b = str(entry.get("name") or "").strip()
        if not name_a or name_a != name_b:
            # 同名是同一文件判定的前提，名字不同一定不是同一个文件
            return False
        fid_a = str(material.get("fid") or "").strip()
        fid_b = str(entry.get("fid") or "").strip()
        if fid_a and fid_b and fid_a == fid_b:
            return True
        md5_a = str(material.get("md5") or "").strip().lower()
        md5_b = str(entry.get("md5") or "").strip().lower()
        if md5_a and md5_b:
            return md5_a == md5_b
        size_a = self._lead_in_to_int(material.get("size"))
        size_b = self._lead_in_to_int(entry.get("size"))
        return size_a > 0 and size_a == size_b

    # ==================== 查重 ====================
    def _lead_in_inspect(self, material: Dict[str, Any], target: Dict[str, Any]) -> Dict[str, Any]:
        """检查目标目录是否已存在该素材文件"""
        material = material if isinstance(material, dict) else {}
        name = str(material.get("name") or "").strip()
        if not name:
            return self._lead_in_error("LEAD_IN_FAILED", "素材信息缺少文件名，请重新解析素材")
        entries, error = self._lead_in_fetch_target_entries(target)
        if error:
            return error
        same_name = [entry for entry in entries if str(entry.get("name") or "").strip() == name]
        if not same_name:
            return self._lead_in_ok({"state": STATE_ABSENT, "existing": None, "message": ""})
        for entry in same_name:
            if self._lead_in_is_same_file(material, entry):
                return self._lead_in_ok(
                    {
                        "state": STATE_EXISTS,
                        "existing": self._lead_in_public_files([entry])[0],
                        "message": "目标目录已存在同一素材文件，无需重复添加",
                    }
                )
        return self._lead_in_ok(
            {
                "state": STATE_CONFLICT,
                "existing": self._lead_in_public_files([same_name[0]])[0],
                "message": "目标目录已存在同名文件或文件夹，但无法确认是同一个文件，未覆盖",
            }
        )

    def lead_in_inspect(self, material: Dict[str, Any], target: Dict[str, Any]) -> Dict[str, Any]:
        """引流：检查目标目录同名文件情况（absent/exists/conflict）"""
        return self._lead_in_inspect(material, target)

    # ==================== 写入结果核验 ====================
    def _lead_in_query_write_state(self, handle: Dict[str, Any]) -> Tuple[str, str]:
        """查询写入任务状态，返回 (done/running/failed/unknown, 说明)"""
        return "unknown", ""

    def lead_in_verify(
        self,
        material: Dict[str, Any],
        target: Dict[str, Any],
        handle: Dict[str, Any],
    ) -> Dict[str, Any]:
        """引流：有界轮询核验素材是否已写入目标目录，绝不谎报 added"""
        material = material if isinstance(material, dict) else {}
        handle = handle if isinstance(handle, dict) else {}
        name = str(material.get("name") or handle.get("name") or "").strip()
        if not name:
            return self._lead_in_error("LEAD_IN_FAILED", "缺少素材文件名，无法核验写入结果")
        attempts = self._lead_in_to_int(getattr(self, "LEAD_IN_MAX_ATTEMPTS", DEFAULT_VERIFY_ATTEMPTS), DEFAULT_VERIFY_ATTEMPTS)
        attempts = max(1, min(attempts, 30))
        try:
            interval = float(getattr(self, "LEAD_IN_POLL_INTERVAL", DEFAULT_VERIFY_INTERVAL))
        except (TypeError, ValueError):
            interval = DEFAULT_VERIFY_INTERVAL
        interval = max(0.0, min(interval, 2.0))
        done_at: Optional[int] = None
        last_message = ""
        for attempt in range(attempts):
            entries, error = self._lead_in_fetch_target_entries(target)
            if entries is not None:
                same_name = [entry for entry in entries if str(entry.get("name") or "").strip() == name]
                for entry in same_name:
                    if self._lead_in_is_same_file(material, entry):
                        return self._lead_in_ok(
                            {
                                "state": STATE_ADDED,
                                "fid": str(entry.get("fid") or ""),
                                "existing": self._lead_in_public_files([entry])[0],
                                "message": "",
                            }
                        )
                if same_name:
                    return self._lead_in_ok(
                        {
                            "state": STATE_FAILED,
                            "fid": "",
                            "existing": self._lead_in_public_files([same_name[0]])[0],
                            "message": "目标目录已存在同名文件或文件夹，未覆盖、未改名",
                        }
                    )
                if done_at is not None and attempt >= done_at + 2:
                    return self._lead_in_ok(
                        {
                            "state": STATE_FAILED,
                            "fid": "",
                            "existing": None,
                            "message": "网盘转存任务已完成，但目标目录中未找到该素材文件（可能被网盘改名），请人工核对",
                        }
                    )
            else:
                if error:
                    last_message = str(error.get("message") or "")
            state, message = self._lead_in_query_write_state(handle)
            if state == "failed":
                return self._lead_in_ok(
                    {
                        "state": STATE_FAILED,
                        "fid": "",
                        "existing": None,
                        "message": message or "网盘转存任务失败",
                    }
                )
            if state == "done" and done_at is None:
                done_at = attempt
            if attempt < attempts - 1 and interval > 0:
                time.sleep(interval)
        return self._lead_in_ok(
            {
                "state": STATE_PENDING,
                "fid": "",
                "existing": None,
                "message": "网盘响应超时，添加结果待确认" + (f"（{last_message}）" if last_message else ""),
            }
        )

    # ==================== 素材凭证补齐 ====================
    def _lead_in_prepare_material(
        self, material: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
        """补全素材的分享凭证，必要时按分享链接重新解析素材"""
        data = dict(material) if isinstance(material, dict) else {}
        pwd_id = str(data.get("pwd_id") or "").strip()
        stoken = str(data.get("stoken") or "").strip()
        if pwd_id and stoken:
            return data, None
        shareurl = str(data.get("shareurl") or data.get("share_url") or "").strip()
        if not shareurl:
            return None, self._lead_in_error("LEAD_IN_FAILED", "素材缺少分享凭证，请重新解析素材")
        resolved = self.lead_in_resolve_material(
            shareurl,
            str(data.get("passcode") or ""),
            str(data.get("fid") or ""),
        )
        if not isinstance(resolved, dict):
            return None, self._lead_in_error("LEAD_IN_FAILED", "重新解析素材失败，请稍后重试")
        if resolved.get("code") != 0:
            return None, resolved
        resolved_data = resolved.get("data") if isinstance(resolved.get("data"), dict) else {}
        data["pwd_id"] = str(resolved_data.get("pwd_id") or "").strip()
        data["stoken"] = str(resolved_data.get("stoken") or "").strip()
        fresh = resolved_data.get("material") if isinstance(resolved_data.get("material"), dict) else {}
        for key in ("fid", "name", "size", "path", "md5", "material_token"):
            if not data.get(key) and fresh.get(key) not in (None, ""):
                data[key] = fresh.get(key)
        if not data.get("pwd_id") or not data.get("stoken"):
            return None, self._lead_in_error("LEAD_IN_FAILED", "素材分享凭证无效，请重新解析素材")
        return data, None
