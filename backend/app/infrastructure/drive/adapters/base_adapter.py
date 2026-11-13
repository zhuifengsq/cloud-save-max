# -*- coding: utf-8 -*-
"""
云盘适配器基类
定义所有网盘适配器必须实现的接口
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Tuple, Optional, Any
import hashlib
import logging
import random
import threading
import time
import uuid

from app.core.runtime.rate_limiter import get_rate_limiter
from app.core.settings import settings


logger = logging.getLogger(__name__)


class BaseCloudDriveAdapter(ABC):
    """云盘适配器抽象基类"""

    SUPPORTS_COMPLETE_LISTING = False
    COMPLETE_LISTING_MAX_PAGES = 1000

    def ls_dir_complete(self, pdir_fid: str) -> Dict:
        """Fail closed unless this concrete adapter explicitly implements strict listing."""
        from .complete_listing import failure, validate_result

        if type(self).__dict__.get("SUPPORTS_COMPLETE_LISTING") is not True:
            return failure("Complete directory listing is unsupported by this adapter")
        try:
            return validate_result(self.ls_dir(pdir_fid, max_items=0, require_complete=True))
        except Exception as exc:
            return failure(str(exc))

    # 网盘类型标识
    DRIVE_TYPE = "base"
    DRIVE_NAME = "基础网盘"
    CONFIG_FORMAT = "raw"
    default_config: dict[str, Any] = {"cookie": ""}
    config_fields: list[dict[str, Any]] = [
        {
            "key": "cookie",
            "label": "Cookie",
            "description": "登录态原文，按驱动要求填写。",
            "input_type": "textarea",
            "required": True,
            "secret": True,
            "placeholder": "",
        }
    ]

    def __init__(
        self,
        cookie: str = "",
        index: int = 0,
        config: dict[str, Any] | None = None,
        *,
        no_login: bool = False,
    ):
        self.config = self.resolve_runtime_config(config=config, cookie=cookie)
        self.cookie = self.serialize_config(self.config)
        self.index = index + 1
        self.is_active = False
        self.nickname = ""
        self.no_login = bool(no_login)

        self._rate_limit_min_interval = 0.05
        self._rate_limit_max_interval = 0.10
        self._rate_limit_lock = threading.Lock()
        self._last_request_at = 0.0

        # 限速与令牌刷新都需要「按账户」而不是「按实例」归并（同一个账号可能同时存在
        # 多个适配器实例：实例缓存 TTL 到期重建、cookie 轮换、并发请求各自 create）。
        # 实例 id 用来把「同一实例内的并发线程」合并到同一把锁上。
        self._instance_id = uuid.uuid4().hex

        # 适配器实例在进程内被多个请求线程、任务执行线程、调度线程共享，
        # 这里记录 init() 的成功时间与结果，供 ensure_initialized() 做去重与串行化。
        self._init_lock = threading.RLock()
        self._init_success_at = 0.0
        self._init_result: Any = None

        self.savepath_fid: Dict[str, str] = {"/": "0"}

    def _rate_limit_key(self) -> str:
        """限速 key：优先「网盘类型 + 账户名」，缺失时用 cookie 指纹，最后退到实例序号。

        旧实现只锁单个实例，同账户多实例并存时限速形同虚设；按账户排队才能真正
        让同一账号的请求串行在网盘侧的配额之内。
        """
        drive_type = str(getattr(self, "DRIVE_TYPE", "base") or "base")
        account = str(getattr(self, "account_name", "") or getattr(self, "_account_name", "") or "")
        if account:
            return f"{drive_type}:{account}"
        raw_cookie = str(getattr(self, "cookie", "") or "")
        if raw_cookie:
            return f"{drive_type}:cookie:{hashlib.md5(raw_cookie.encode('utf-8')).hexdigest()[:12]}"
        return f"{drive_type}:index:{self.index}"

    def _token_refresh_key(self) -> str:
        """令牌刷新 single-flight 的 key（同一实例内并发请求合并为一次刷新）。

        注意：不同实例各自持有自己的 token 状态，合并跨实例刷新需要共享令牌存储
        （本期不做，见 docs/architecture-evolution-plan.md §10.5）。
        """
        return f"token:{self._rate_limit_key()}:{self._instance_id}"

    def _rate_limit_wait(self) -> None:
        get_rate_limiter().wait(
            self._rate_limit_key(),
            self._rate_limit_min_interval,
            self._rate_limit_max_interval,
        )

    def _throttle_request(self) -> None:
        # 灰度开关：use_keyed_rate_limiter=False 时回退到旧的「每实例一把锁 + 随机 sleep」。
        if bool(getattr(settings, "use_keyed_rate_limiter", False)):
            self._rate_limit_wait()
            return
        with self._rate_limit_lock:
            now = time.monotonic()
            if self._last_request_at > 0:
                interval = random.uniform(
                    self._rate_limit_min_interval, self._rate_limit_max_interval
                )
                elapsed = now - self._last_request_at
                if elapsed < interval:
                    time.sleep(interval - elapsed)
                    now = time.monotonic()
            self._last_request_at = now

    @classmethod
    def get_config_meta(cls) -> dict[str, Any]:
        return {
            "drive_name": getattr(cls, "DRIVE_NAME", getattr(cls, "DRIVE_TYPE", "base")),
            "config_format": getattr(cls, "CONFIG_FORMAT", "raw"),
            "default_config": dict(getattr(cls, "default_config", {}) or {}),
            "config_fields": list(getattr(cls, "config_fields", []) or []),
        }

    @classmethod
    def normalize_config(cls, config: dict[str, Any] | None) -> dict[str, Any]:
        result = dict(getattr(cls, "default_config", {}) or {})
        for key, value in (config or {}).items():
            result[key] = value
        return result

    @classmethod
    def deserialize_cookie(cls, cookie: str | None) -> dict[str, Any]:
        config = cls.normalize_config(None)
        raw_cookie = str(cookie or "").strip()
        if not raw_cookie:
            return config
        if getattr(cls, "CONFIG_FORMAT", "raw") == "kv":
            parsed: dict[str, Any] = {}
            for chunk in raw_cookie.split(";"):
                chunk = chunk.strip()
                if not chunk or "=" not in chunk:
                    continue
                key, value = chunk.split("=", 1)
                parsed[key.strip()] = value.strip()
            for key, default_value in config.items():
                if key not in parsed:
                    continue
                if isinstance(default_value, bool):
                    config[key] = str(parsed[key]).strip().lower() in ("1", "true", "yes", "on")
                elif isinstance(default_value, int) and not isinstance(default_value, bool):
                    try:
                        config[key] = int(parsed[key])
                    except ValueError:
                        config[key] = parsed[key]
                else:
                    config[key] = parsed[key]
            for key, value in parsed.items():
                if key not in config:
                    config[key] = value
            return config
        config[cls.primary_config_key()] = raw_cookie
        return config

    @classmethod
    def resolve_runtime_config(
        cls,
        *,
        config: dict[str, Any] | None = None,
        cookie: str | None = None,
    ) -> dict[str, Any]:
        if config is not None:
            return cls.normalize_config(config)
        return cls.deserialize_cookie(cookie)

    @classmethod
    def serialize_config(cls, config: dict[str, Any] | None) -> str:
        payload = cls.normalize_config(config)
        if getattr(cls, "CONFIG_FORMAT", "raw") == "kv":
            parts: list[str] = []
            for key, value in payload.items():
                if not cls.keep_value(value):
                    continue
                parts.append(f"{key}={str(value).strip() if not isinstance(value, bool) else str(value)}")
            return ";".join(parts)
        return str(payload.get(cls.primary_config_key(), "") or "").strip()

    @classmethod
    def primary_config_key(cls) -> str:
        fields = getattr(cls, "config_fields", []) or []
        if fields and fields[0].get("key"):
            return str(fields[0]["key"])
        defaults = getattr(cls, "default_config", {}) or {}
        if defaults:
            return next(iter(defaults.keys()))
        return "cookie"

    @staticmethod
    def keep_value(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and not isinstance(value, bool):
            return True
        return str(value or "").strip() != ""

    def ensure_initialized(self, *, ttl_seconds: float = 0.0) -> Any:
        """线程安全地初始化账户，ttl_seconds 内成功过就复用上次结果、不重复登录。

        同一账户的适配器实例会被多个请求线程、任务执行线程、调度线程共享。
        串行化 init() 可以避免并发刷新 token / 并发登录，也避免账号状态被同时改写；
        ttl_seconds 内的跳过则省掉重复的网络登录（每次 init 都是一次账户信息请求）。

        init() 抛出的异常（例如 cloud189 验证码流程的 DriveAuthRequired）保持原样
        向上抛出，只清掉成功标记，以便下次调用重新尝试。
        """
        with self._init_lock:
            if ttl_seconds > 0 and self.is_active and self._init_success_at > 0:
                if (time.monotonic() - self._init_success_at) < ttl_seconds:
                    return self._init_result if self._init_result is not None else True
            try:
                result = self.init()
            except Exception:
                self._init_success_at = 0.0
                self._init_result = None
                raise
            if result:
                self._init_success_at = time.monotonic()
                self._init_result = result
            else:
                self._init_success_at = 0.0
                self._init_result = None
            return result

    @abstractmethod
    def init(self) -> Any:
        """
        初始化账户，验证 cookie 有效性
        Returns:
            成功返回账户信息 dict，失败返回 False
        """
        pass

    @abstractmethod
    def get_stoken(self, pwd_id: str, passcode: str = "") -> Dict:
        """
        获取分享令牌，验证资源有效性
        Args:
            pwd_id: 分享ID
            passcode: 提取码
        Returns:
            响应字典，包含 status, data, message 等字段
        """
        pass

    @abstractmethod
    def get_detail(
        self,
        pwd_id: str,
        stoken: str,
        pdir_fid: str,
        _fetch_share: int = 0,
        fetch_share_full_path: int = 0,
    ) -> Dict:
        """
        获取分享文件详情列表
        Args:
            pwd_id: 分享ID
            stoken: 分享令牌
            pdir_fid: 父目录ID
            _fetch_share: 是否获取分享信息
            fetch_share_full_path: 是否获取完整路径
        Returns:
            响应字典，包含 code, data.list 等字段
        """
        pass

    @abstractmethod
    def ls_dir(self, pdir_fid: str, max_items: int = 0, **kwargs) -> Dict:
        """
        列出目录内容
        Args:
            pdir_fid: 目录ID
            max_items: 最大返回条目数，0 表示不限制（全量加载）
        Returns:
            响应字典，包含 code, data.list 等字段
        """
        pass

    @abstractmethod
    def save_file(
        self,
        fid_list: List[str],
        fid_token_list: List[str],
        to_pdir_fid: str,
        pwd_id: str,
        stoken: str,
    ) -> Dict:
        """
        转存文件到指定目录
        Args:
            fid_list: 文件ID列表
            fid_token_list: 文件token列表
            to_pdir_fid: 目标目录ID
            pwd_id: 分享ID
            stoken: 分享令牌
        Returns:
            响应字典，包含 code, data.task_id 等字段
        """
        pass

    @abstractmethod
    def query_task(self, task_id: str) -> Dict:
        """
        查询转存任务状态
        Args:
            task_id: 任务ID
        Returns:
            响应字典，包含任务状态信息
        """
        pass

    @abstractmethod
    def mkdir(self, dir_path: str) -> Dict:
        """
        创建目录
        Args:
            dir_path: 目录路径
        Returns:
            响应字典，包含 code, data.fid 等字段
        """
        pass

    @abstractmethod
    def rename(self, fid: str, file_name: str) -> Dict:
        """
        重命名文件
        Args:
            fid: 文件ID
            file_name: 新文件名
        Returns:
            响应字典
        """
        pass

    @abstractmethod
    def delete(self, filelist: List[str]) -> Dict:
        """
        删除文件
        Args:
            filelist: 文件ID列表
        Returns:
            响应字典
        """
        pass

    @abstractmethod
    def get_fids(self, file_paths: List[str]) -> List[Dict]:
        """
        根据路径获取文件ID
        Args:
            file_paths: 文件路径列表
        Returns:
            包含 file_path 和 fid 的字典列表
        """
        pass

    @abstractmethod
    def extract_url(self, url: str) -> Tuple[Optional[str], str, Any, List]:
        """
        解析分享链接
        Args:
            url: 分享链接
        Returns:
            (pwd_id, passcode, pdir_fid, paths) 元组
        """
        pass

    def unarchive(self, fid: str, to_pdir_fid: str) -> Dict:
        """
        云解压文件（可选实现）
        Args:
            fid: 压缩包文件ID
            to_pdir_fid: 解压到的目录ID
        Returns:
            响应字典，包含 code, data.task_id 等字段
        """
        return {"code": 0, "message": "success", "data": {"task_id": ""}}

    def move_files(self, fids: List[str], to_pdir_fid: str) -> Dict:
        """
        批量移动文件（可选实现）
        Args:
            fids: 文件ID列表
            to_pdir_fid: 目标目录ID
        Returns:
            响应字典
        """
        return {"code": 0, "message": "success"}

    def create_share_link(self, file_path: str, title: str) -> Dict[str, str]:
        """Create a link for a saved cloud-drive path when the drive supports sharing."""
        raise NotImplementedError(f"{self.DRIVE_NAME}暂不支持创建分享链接")

    # ------------------------------------------------------------------
    # 分享链接失效判定
    #
    # 各网盘分享校验失败时的响应结构、错误码体系完全不同，这里只统一「结论」：
    #   invalid    链接已失效/被取消/文件已删除（可以确定不可用，需要提醒用户）
    #   passcode   需要提取码或提取码错误
    #   captcha    需要验证码/人工验证
    #   auth       账号未登录、Cookie 失效、认证失败
    #   throttled  限流、访问频率过高
    #   pending    等待审核等暂时不可用
    #   unknown    无法判断（调用方按「检测失败」处理，不当作链接失效）
    # 各网盘适配器按自己的错误码表重写本方法；未重写的网盘一律 unknown。
    # 生成分享链接检测、任务引流等场景共用本方法，避免各写一份判定表。
    # ------------------------------------------------------------------
    SHARE_FAILURE_KINDS = (
        "invalid",
        "passcode",
        "captcha",
        "auth",
        "throttled",
        "pending",
        "unknown",
    )

    def classify_share_failure(self, response: Dict[str, Any]) -> Dict[str, str]:
        """把一次分享校验失败响应归类成统一的结论（默认无法判断）。"""
        message = ""
        if isinstance(response, dict):
            message = str(response.get("message") or "").strip()
        return {"kind": "unknown", "message": message}

    # ------------------------------------------------------------------
    # 任务引流（引流文件）可选能力
    #
    # 任务上的「添加引流文件」只走下面这组统一操作：
    #   解析素材 -> 定位目标目录 -> 查重 -> 写入 -> 确认写入结果
    # 公共服务层、接口层、前端不得出现网盘类型分支；网盘之间的差异只能留在
    # 本能力接口与各自适配器实现里。声明 SUPPORTS_DRAMA_LEAD_IN = True 的
    # 适配器必须实现全部五个方法；未声明的适配器对外显示「暂不支持引流」。
    #
    # 所有方法的返回值统一为：
    #   {"code": 0, "message": "", "data": {...}}            成功
    #   {"code": "<LEAD_IN_XXX>", "message": "中文原因"}      失败（code 为字符串错误码）
    # ------------------------------------------------------------------
    SUPPORTS_DRAMA_LEAD_IN = False
    # 是否支持「本账号文件」素材模式（账号内精确路径定位 + 网盘内部复制）
    SUPPORTS_LEAD_IN_OWN_FILE = False
    # 是否支持浏览本账号目录（只读，用于设置页选择本账号文件）
    SUPPORTS_LEAD_IN_OWN_BROWSE = False

    def lead_in_capability(self) -> Dict[str, Any]:
        """返回本适配器的引流能力描述，供系统设置与任务按钮判断。"""
        supported = bool(getattr(type(self), "SUPPORTS_DRAMA_LEAD_IN", False))
        return {
            "drive_type": self.DRIVE_TYPE,
            "drive_name": self.DRIVE_NAME,
            "supported": supported,
            "reason": "" if supported else f"{self.DRIVE_NAME}暂不支持引流",
            "own_file_supported": bool(
                getattr(type(self), "SUPPORTS_LEAD_IN_OWN_FILE", False)
            ),
            "own_file_browse_supported": bool(
                getattr(type(self), "SUPPORTS_LEAD_IN_OWN_BROWSE", False)
            ),
        }

    def _lead_in_unsupported(self) -> Dict[str, Any]:
        return {
            "code": "LEAD_IN_UNSUPPORTED",
            "message": f"{self.DRIVE_NAME}暂不支持引流",
            "data": None,
        }

    def _lead_in_own_file_unsupported(self) -> Dict[str, Any]:
        return {
            "code": "LEAD_IN_UNSUPPORTED",
            "message": f"{self.DRIVE_NAME}暂不支持本账号文件引流",
            "data": None,
        }

    def lead_in_resolve_material(
        self,
        shareurl: str,
        passcode: str = "",
        selected_fid: str = "",
    ) -> Dict[str, Any]:
        """解析素材分享链接，定位唯一的素材文件。

        Args:
            shareurl: 管理员配置的素材分享链接（必须是本网盘认可的分享地址）
            passcode: 提取码（百度需要；夸克可为空）
            selected_fid: 管理员显式选定的分享内文件 ID
        Returns:
            {"code": 0, "data": {
                "files": [{"fid", "name", "size", "dir"}],   # 分享内可供选择的文件
                "material": {"fid", "name", "size", "path", "material_token"},
                "pwd_id": str, "stoken": str,                # 供后续写入复用
            }}
            分享内不唯一且未提供 selected_fid 时必须返回
            {"code": "LEAD_IN_MATERIAL_AMBIGUOUS"}，禁止默认取第一项；
            需要提取码时返回 {"code": "LEAD_IN_PASSCODE_REQUIRED"}。
        """
        return self._lead_in_unsupported()

    def lead_in_resolve_dir(self, dir_path: str) -> Dict[str, Any]:
        """按任务保存目录定位目标目录（必须已存在且为文件夹，由适配器判定）。

        Returns:
            {"code": 0, "data": {
                "dir": {"fid": str, "path": str, "name": str},
                "entries": [{"fid", "name", "size", "dir"}],  # 目录内已有内容，供查重
            }}
            目录不存在返回 {"code": "LEAD_IN_DIR_NOT_FOUND"}；
            路径是文件/根目录/无法精确定位返回 {"code": "LEAD_IN_DIR_INVALID"}。
        """
        return self._lead_in_unsupported()

    def lead_in_inspect(
        self,
        material: Dict[str, Any],
        target: Dict[str, Any],
    ) -> Dict[str, Any]:
        """查重：判断目标目录里是否已存在同一素材文件。

        Args:
            material: lead_in_resolve_material 返回的 data.material
            target: lead_in_resolve_dir 返回的 data
        Returns:
            {"code": 0, "data": {"state": "absent" | "exists" | "conflict",
                                 "existing": {...} | None,
                                 "message": ""}}
            exists 表示可确认是同一文件；conflict 表示同名但无法确认是同一文件，
            不得覆盖、改名或删除。
        """
        return self._lead_in_unsupported()

    def lead_in_add(
        self,
        material: Dict[str, Any],
        target: Dict[str, Any],
    ) -> Dict[str, Any]:
        """把选定的素材文件写入目标目录（只写这一个文件，绝不整目录转存）。

        Returns:
            {"code": 0, "data": {"handle": {...}, "sync": bool}}
            handle 为后续 lead_in_verify 需要的信息（网盘任务 ID / 预期文件名等）。
        """
        return self._lead_in_unsupported()

    def lead_in_verify(
        self,
        material: Dict[str, Any],
        target: Dict[str, Any],
        handle: Dict[str, Any],
    ) -> Dict[str, Any]:
        """等待并核验写入结果（查询网盘任务 + 核对目标目录）。

        Returns:
            {"code": 0, "data": {"state": "added" | "pending" | "failed",
                                 "fid": str, "existing": {...} | None,
                                 "message": ""}}
            超时/结果未知必须返回 pending，由调用方提示「待确认」，不得当作成功。
            本方法内部只能做有界查询，不得长时间阻塞。
        """
        return self._lead_in_unsupported()

    def lead_in_resolve_own_file(self, file_path: str) -> Dict[str, Any]:
        """在当前账号内按完整路径精确定位素材文件（只允许精确路径匹配，禁止按文件名全盘搜索）。

        Returns:
            {"code": 0, "data": {"file": {
                "fid": str, "name": str, "path": str,
                "size": int | None, "md5": str, "dir": False,
            }}}
            路径为根/空/包含 ".." 或 "://" -> {"code": "LEAD_IN_DIR_INVALID", "message": "..."}
            文件不存在 -> {"code": "LEAD_IN_MATERIAL_UNAVAILABLE", "message": "..."}
            路径指向文件夹 -> {"code": "LEAD_IN_MATERIAL_UNAVAILABLE", "message": "只能选择文件，不能选择文件夹"}
        """
        return self._lead_in_own_file_unsupported()

    def lead_in_add_own_file(
        self,
        material: Dict[str, Any],
        target: Dict[str, Any],
    ) -> Dict[str, Any]:
        """把当前账号内的素材文件「内部复制」到目标目录，只复制这一个文件。

        严禁：下载再上传、跨账号/跨网盘复制、按文件名全盘搜索后复制、整目录复制。
        Returns:
            {"code": 0, "data": {"handle": {...}, "sync": bool}}
            失败时返回适配器错误码（含中文 message），由服务层映射。
        """
        return self._lead_in_own_file_unsupported()

    def lead_in_list_own_dir(self, dir_path: str) -> Dict[str, Any]:
        """浏览当前账号内的目录（只读），用于设置页选择「本账号文件」素材。

        Returns:
            {"code": 0, "message": "", "data": {"path": str, "fid": str, "entries": [
                {"fid": str, "name": str, "dir": bool, "size": int, "path": str},
            ]}}
            目录不存在 -> LEAD_IN_DIR_NOT_FOUND；路径不合法 -> LEAD_IN_DIR_INVALID；
            读取失败 -> LEAD_IN_DIR_LIST_FAILED；超时 -> LEAD_IN_TIMEOUT。
        """
        return {
            "code": "LEAD_IN_UNSUPPORTED",
            "message": f"{self.DRIVE_NAME}暂不支持浏览本账号目录",
            "data": None,
        }

    def get_account_info(self) -> Any:
        """获取账户信息（可选实现）"""
        return False

    def get_account_config(self) -> Dict[str, Any]:
        """获取账号配置/概览信息（用于管理页展示）"""
        return {
            "drive_type": self.DRIVE_TYPE,
            "drive_name": self.DRIVE_NAME,
            "nickname": self.nickname or "",
            "username": "",
            "used_space": None,
            "total_space": None,
            "raw": None,
        }

    def sign_in(self) -> Dict[str, Any]:
        return {"supported": False, "message": "not supported"}

    def export_runtime_config(self) -> dict[str, Any]:
        return self.deserialize_cookie(self.cookie)

    def update_savepath_fid(self, tasklist: List[Dict]) -> bool:
        """
        更新保存路径的 fid 映射
        Args:
            tasklist: 任务列表
        Returns:
            是否成功
        """
        # 通用实现，子类可重写
        import re

        dir_paths = [
            re.sub(r"/{2,}", "/", f"/{item['savepath']}")
            for item in tasklist
        ]
        if not dir_paths:
            return False

        dir_paths_exist_arr = self.get_fids(dir_paths)
        dir_paths_exist = [item["file_path"] for item in dir_paths_exist_arr]

        # 创建不存在的目录
        dir_paths_unexist = list(set(dir_paths) - set(dir_paths_exist) - set(["/"]))
        for dir_path in dir_paths_unexist:
            mkdir_return = self.mkdir(dir_path)
            if mkdir_return.get("code") == 0:
                new_dir = mkdir_return["data"]
                dir_paths_exist_arr.append(
                    {"file_path": dir_path, "fid": new_dir["fid"]}
                )
            else:
                logger.warning("创建文件夹：%s 失败, %s", dir_path, mkdir_return.get("message", "未知错误"))

        # 储存目标目录的fid
        for dir_path in dir_paths_exist_arr:
            self.savepath_fid[dir_path["file_path"]] = dir_path["fid"]

        return True
