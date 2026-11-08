# -*- coding: utf-8 -*-
"""
夸克网盘适配器
适配现有 Quark 类到统一接口
"""
import sys
import os
import logging
from typing import Dict, List, Tuple, Optional, Any

# 添加父目录到路径，以便导入 quark_auto_save
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.infrastructure.drive.adapters.base_adapter import BaseCloudDriveAdapter
from app.infrastructure.drive.adapters.lead_in_support import DramaLeadInMixin


logger = logging.getLogger(__name__)


class QuarkAdapter(DramaLeadInMixin, BaseCloudDriveAdapter):
    """夸克网盘适配器"""

    DRIVE_TYPE = "quark"
    DRIVE_NAME = "夸克网盘"
    CONFIG_FORMAT = "raw"
    default_config = {
        "cookie": "",
    }
    config_fields = [
        {
            "key": "cookie",
            "label": "Cookie",
            "description": "夸克网盘登录 Cookie 原文；如需移动端分享兼容，可一并带上 kps、sign、vcode。",
            "input_type": "textarea",
            "required": True,
            "secret": True,
            "placeholder": "__puus=...; kps=...; sign=...; vcode=...",
        }
    ]
    BASE_URL = "https://drive-pc.quark.cn"
    BASE_URL_APP = "https://drive-m.quark.cn"
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) quark-cloud-drive/3.14.2 Chrome/112.0.5615.165 Electron/24.1.3.8 Safari/537.36 Channel/pckk_other_ch"

    # 是否支持"追剧任务引流文件"能力
    SUPPORTS_DRAMA_LEAD_IN = True
    # 是否支持「本账号文件」素材模式（账号内精确路径定位 + 网盘内部复制）
    SUPPORTS_LEAD_IN_OWN_FILE = True
    # 是否支持浏览本账号目录（设置页选择「本账号文件」素材时使用）
    SUPPORTS_LEAD_IN_OWN_BROWSE = True

    def __init__(
        self,
        cookie: str = "",
        index: int = 0,
        config: dict | None = None,
        account_name: str = "",
        no_login: bool = False,
    ):
        super().__init__(cookie, index, config=config, no_login=no_login)
        self.mparam = self._match_mparam_form_cookie(cookie)

    def _match_mparam_form_cookie(self, cookie: str) -> Dict:
        """从 cookie 中提取移动端参数"""
        import re

        mparam = {}
        kps_match = re.search(r"(?<!\w)kps=([a-zA-Z0-9%+/=]+)[;&]?", cookie)
        sign_match = re.search(r"(?<!\w)sign=([a-zA-Z0-9%+/=]+)[;&]?", cookie)
        vcode_match = re.search(r"(?<!\w)vcode=([a-zA-Z0-9%+/=]+)[;&]?", cookie)
        if kps_match and sign_match and vcode_match:
            mparam = {
                "kps": kps_match.group(1).replace("%25", "%"),
                "sign": sign_match.group(1).replace("%25", "%"),
                "vcode": vcode_match.group(1).replace("%25", "%"),
            }
        return mparam
    def convert_bytes(self, b):
        '''
        将字节转换为 MB GB TB
        :param b: 字节数
        :return: 返回 MB GB TB
        '''
        units = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
        i = 0
        while b >= 1024 and i < len(units) - 1:
            b /= 1024
            i += 1
        return f"{b:.2f} {units[i]}"
    def _send_request(self, method: str, url: str, **kwargs):
        """发送 HTTP 请求"""
        import requests
        import random
        from datetime import datetime
        self._throttle_request()
        
        headers = {
            "cookie": self.cookie,
            "content-type": "application/json",
            "user-agent": self.USER_AGENT,
        }
        if "headers" in kwargs:
            headers = kwargs["headers"]
            del kwargs["headers"]
        # Mobile credentials apply to accessing someone else's sharepage only.
        # Creating a share for our own files uses the PC API and Cookie.
        if self.mparam and url.startswith(f"{self.BASE_URL}/1/clouddrive/share/sharepage/"):
            url = url.replace(self.BASE_URL, self.BASE_URL_APP)
            kwargs["params"].update(
                {
                    "device_model": "M2011K2C",
                    "entry": "default_clouddrive",
                    "_t_group": "0%3A_s_vp%3A1",
                    "dmn": "Mi%2B11",
                    "fr": "android",
                    "pf": "3300",
                    "bi": "35937",
                    "ve": "7.4.5.680",
                    "ss": "411x875",
                    "mi": "M2011K2C",
                    "nt": "5",
                    "nw": "0",
                    "kt": "4",
                    "pr": "ucpro",
                    "sv": "release",
                    "dt": "phone",
                    "data_from": "ucapi",
                    "kps": self.mparam.get("kps"),
                    "sign": self.mparam.get("sign"),
                    "vcode": self.mparam.get("vcode"),
                    "app": "clouddrive",
                    "kkkk": "1",
                }
            )
            del headers["cookie"]
        try:
            kwargs.setdefault("timeout", (5, 30))
            response = requests.request(method, url, headers=headers, **kwargs)
            return response
        except Exception as e:
            logger.exception("_send_request error: %s", e)
            fake_response = requests.Response()
            fake_response.status_code = 500
            fake_response._content = (
                b'{"status": 500, "code": 1, "message": "request error"}'
            )
            return fake_response

    def init(self) -> Any:
        """初始化账户"""
        account_info = self.get_account_info()
        if account_info:
            self.is_active = True
            self.nickname = account_info["nickname"]
            return account_info
        else:
            return False

    def get_account_info(self) -> Any:
        """获取账户信息"""
        url = "https://pan.quark.cn/account/info"
        querystring = {"fr": "pc", "platform": "pc"}
        response = self._send_request("GET", url, params=querystring).json()
        if response.get("data"):
            return response["data"]
        else:
            return False

    def get_account_config(self) -> Dict[str, Any]:
        """获取夸克账户配置/容量信息"""
        account_info = self.get_account_info() or {}
        member_info = self._get_member_info()
        member_data = member_info.get("data") if isinstance(member_info, dict) else None

        nickname = (
            account_info.get("nickname")
            or account_info.get("nick_name")
            or self.nickname
            or f"夸克用户{self.index}"
        )
        if nickname:
            self.nickname = nickname

        return {
            "drive_type": self.DRIVE_TYPE,
            "drive_name": self.DRIVE_NAME,
            "nickname": nickname,
            "username": nickname,
            "used_space": member_data.get("use_capacity") if isinstance(member_data, dict) else None,
            "total_space": member_data.get("total_capacity") if isinstance(member_data, dict) else None,
            "member_type": member_data.get("member_type") if isinstance(member_data, dict) else None,
            "member_status": member_data.get("member_status") if isinstance(member_data, dict) else None,
            "raw": {
                "account_info": account_info or None,
                "member_info": member_data,
            },
        }

    def _get_member_info(self) -> Dict[str, Any]:
        url = f"{self.BASE_URL}/1/clouddrive/member"
        querystring = {
            "pr": "ucpro",
            "fr": "pc",
            "uc_param_str": "",
            "fetch_subscribe": "true",
            "_ch": "home",
            "fetch_identity": "true",
        }
        response = self._send_request("GET", url, params=querystring).json()
        if response.get("code") == 0 and response.get("data"):
            return response
        return {}

    def get_stoken(self, pwd_id: str, passcode: str = "") -> Dict:
        """获取分享令牌"""
        url = f"{self.BASE_URL}/1/clouddrive/share/sharepage/token"
        querystring = {"pr": "ucpro", "fr": "pc"}
        payload = {"pwd_id": pwd_id, "passcode": passcode}
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    def get_detail(
        self,
        pwd_id: str,
        stoken: str,
        pdir_fid: str,
        _fetch_share: int = 0,
        fetch_share_full_path: int = 0,
        *,
        first_page: bool = False,
    ) -> Dict:
        """获取分享文件详情"""
        list_merge = []
        page = 1
        while True:
            url = f"{self.BASE_URL}/1/clouddrive/share/sharepage/detail"
            querystring = {
                "pr": "ucpro",
                "fr": "pc",
                "pwd_id": pwd_id,
                "stoken": stoken,
                "pdir_fid": pdir_fid,
                "force": "0",
                "_page": page,
                "_size": "50",
                "_fetch_banner": "0",
                "_fetch_share": _fetch_share,
                "_fetch_total": "1",
                "_sort": "file_type:asc,updated_at:desc",
                "ver": "2",
                "fetch_share_full_path": fetch_share_full_path,
            }
            response = self._send_request("GET", url, params=querystring).json()
            if response["code"] != 0 or first_page:
                return response
            if response["data"]["list"]:
                list_merge += response["data"]["list"]
                page += 1
            else:
                break
            if len(list_merge) >= response["metadata"]["_total"]:
                break
        response["data"]["list"] = list_merge
        return response

    def ls_dir(self, pdir_fid: str, max_items: int = 0, **kwargs) -> Dict:
        """列出目录内容"""
        list_merge = []
        page = 1
        while True:
            url = f"{self.BASE_URL}/1/clouddrive/file/sort"
            querystring = {
                "pr": "ucpro",
                "fr": "pc",
                "uc_param_str": "",
                "pdir_fid": pdir_fid if pdir_fid else "0",
                "_page": page,
                "_size": "50",
                "_fetch_total": "1",
                "_fetch_sub_dirs": "0",
                "_sort": "file_type:asc,updated_at:desc",
                "_fetch_full_path": kwargs.get("fetch_full_path", 0),
                "fetch_all_file": 1,
                "fetch_risk_file_name": 1,
            }
            response = self._send_request("GET", url, params=querystring).json()
            if response["code"] != 0:
                return response
            if response["data"]["list"]:
                list_merge += response["data"]["list"]
                page += 1
            else:
                break
            # max_items 限量：达到上限后提前终止分页
            if max_items > 0 and len(list_merge) >= max_items:
                list_merge = list_merge[:max_items]
                break
            if len(list_merge) >= response["metadata"]["_total"]:
                break
        response["data"]["list"] = list_merge
        return response

    def save_file(
        self,
        fid_list: List[str],
        fid_token_list: List[str],
        to_pdir_fid: str,
        pwd_id: str,
        stoken: str,
    ) -> Dict:
        """转存文件"""
        import random
        from datetime import datetime

        url = f"{self.BASE_URL}/1/clouddrive/share/sharepage/save"
        querystring = {
            "pr": "ucpro",
            "fr": "pc",
            "uc_param_str": "",
            "app": "clouddrive",
            "__dt": int(random.uniform(1, 5) * 60 * 1000),
            "__t": datetime.now().timestamp(),
        }
        payload = {
            "fid_list": fid_list,
            "fid_token_list": fid_token_list,
            "to_pdir_fid": to_pdir_fid,
            "pwd_id": pwd_id,
            "stoken": stoken,
            "pdir_fid": "0",
            "scene": "link",
        }
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    def query_task(self, task_id: str) -> Dict:
        """查询任务状态"""
        import time
        import random
        from datetime import datetime

        retry_index = 0
        while True:
            url = f"{self.BASE_URL}/1/clouddrive/task"
            querystring = {
                "pr": "ucpro",
                "fr": "pc",
                "uc_param_str": "",
                "task_id": task_id,
                "retry_index": retry_index,
                "__dt": int(random.uniform(1, 5) * 60 * 1000),
                "__t": datetime.now().timestamp(),
            }
            response = self._send_request("GET", url, params=querystring).json()
            if response["status"] != 200:
                logger.warning("查询任务状态失败：%s", response)
                return response
            if response["data"]["status"] == 2:
                break
            else:
                if retry_index == 0:
                    pass
                retry_index += 1
                time.sleep(0.500)
        return response

    def mkdir(self, dir_path: str) -> Dict:
        """创建目录"""
        url = f"{self.BASE_URL}/1/clouddrive/file"
        querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        payload = {
            "pdir_fid": "0",
            "file_name": "",
            "dir_path": dir_path,
            "dir_init_lock": False,
        }
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    def rename(self, fid: str, file_name: str) -> Dict:
        """重命名文件"""
        url = f"{self.BASE_URL}/1/clouddrive/file/rename"
        querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        payload = {"fid": fid, "file_name": file_name}
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    def delete(self, filelist: List[str]) -> Dict:
        """删除文件"""
        url = f"{self.BASE_URL}/1/clouddrive/file/delete"
        querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        payload = {"action_type": 2, "filelist": filelist, "exclude_fids": []}
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    def get_fids(self, file_paths: List[str]) -> List[Dict]:
        """根据路径获取文件ID"""
        fids = []
        while True:
            url = f"{self.BASE_URL}/1/clouddrive/file/info/path_list"
            querystring = {"pr": "ucpro", "fr": "pc"}
            payload = {"file_path": file_paths[:50], "namespace": "0"}
            response = self._send_request(
                "POST", url, json=payload, params=querystring
            ).json()
            if response["code"] == 0:
                fids += response["data"]
                file_paths = file_paths[50:]
            else:
                logger.warning("获取目录ID：失败, %s", response.get("message"))
                break
            if len(file_paths) == 0:
                break
        return fids

    def create_share_link(self, file_path: str, title: str) -> Dict[str, str]:
        """Share the exact saved folder through Quark's share task API."""
        import time
        from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

        path = "/" + "/".join(part for part in str(file_path or "").replace("\\", "/").split("/") if part)
        if path == "/":
            raise ValueError("不能分享网盘根目录")
        candidates = self.get_fids([path]) or []
        folder = next(
            (item for item in candidates if str(item.get("file_path") or item.get("path") or "").rstrip("/") == path),
            None,
        )
        fid = str((folder or {}).get("fid") or "").strip()
        if not fid:
            raise ValueError("保存目录不存在，请先执行转存任务")
        if folder.get("dir") is False or folder.get("is_dir") is False:
            raise ValueError("保存路径不是文件夹")

        listing = self.ls_dir(fid, max_items=1)
        if not isinstance(listing, dict) or listing.get("code") != 0:
            raise RuntimeError("无法读取保存目录")
        if not (((listing.get("data") or {}).get("list")) or []):
            raise ValueError("保存目录为空，请先执行转存任务")

        params = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        def read_response(method: str, endpoint: str, **kwargs) -> dict:
            request_params = kwargs.pop("params", params)
            http_response = self._send_request(
                method, f"{self.BASE_URL}/1/clouddrive/{endpoint}", params=request_params, timeout=20, **kwargs
            )
            status_code = getattr(http_response, "status_code", None)
            if isinstance(status_code, int) and status_code >= 400:
                raise RuntimeError(f"夸克分享接口请求失败（HTTP {status_code}），请检查账号登录状态")
            try:
                response = http_response.json()
            except ValueError as exc:
                status_text = f"HTTP {status_code}" if isinstance(status_code, int) else "未知状态"
                raise RuntimeError(f"夸克分享接口返回非 JSON（{status_text}），请检查账号 Cookie 或稍后重试") from exc
            if not isinstance(response, dict):
                raise RuntimeError("夸克分享接口没有返回有效数据")
            if response.get("code", 0) != 0:
                raise RuntimeError(str(response.get("message") or "夸克分享接口调用失败"))
            return response.get("data") or {}

        created = read_response(
            "POST", "share",
            json={"fid_list": [fid], "title": str(title or path.rsplit("/", 1)[-1]), "url_type": 1, "expired_type": 1},
        )
        share_id = str(created.get("share_id") or "").strip()
        task_id = str(created.get("task_id") or "").strip()
        if not share_id and not task_id:
            raise RuntimeError("夸克没有返回分享任务 ID")
        for attempt in range(20):
            if share_id:
                break
            task_data = read_response("GET", "task", params={**params, "task_id": task_id, "retry_index": attempt})
            share_id = str(task_data.get("share_id") or "").strip()
            if not share_id:
                time.sleep(0.5)
        if not share_id:
            raise RuntimeError("夸克生成分享链接超时，请稍后重试")

        detail = read_response("POST", "share/password", json={"share_id": share_id})
        share_url = str(detail.get("share_url") or "").strip()
        if not share_url.startswith("https://"):
            raise RuntimeError("夸克没有返回有效分享链接")
        passcode = str(detail.get("passcode") or "").strip()
        if passcode:
            parts = urlsplit(share_url)
            query = dict(parse_qsl(parts.query, keep_blank_values=True))
            if "pwd" not in query:
                query["pwd"] = passcode
                share_url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        return {
            "share_url": share_url,
            "share_id": share_id,
            "title": str(detail.get("title") or title or ""),
            "passcode": passcode,
        }

    def extract_url(self, url: str) -> Tuple[Optional[str], str, Any, List]:
        """解析分享链接"""
        import re
        import urllib.parse

        # pwd_id
        match_id = re.search(r"/s/(\w+)", url)
        pwd_id = match_id.group(1) if match_id else None
        # passcode
        match_pwd = re.search(r"pwd=(\w+)", url)
        passcode = match_pwd.group(1) if match_pwd else ""
        # path: fid-name
        paths = []
        matches = re.findall(r"/(\w{32})-?([^/]+)?", url)
        for match in matches:
            fid = match[0]
            name = urllib.parse.unquote(match[1]).replace("*101", "-")
            paths.append({"fid": fid, "name": name})
        pdir_fid = paths[-1]["fid"] if matches else 0
        return pwd_id, passcode, pdir_fid, paths

    # ==================== 任务引流（引流文件） ====================

    def lead_in_resolve_material(
        self,
        shareurl: str,
        passcode: str = "",
        selected_fid: str = "",
    ) -> Dict[str, Any]:
        """解析夸克分享链接，定位唯一的引流素材文件"""
        shareurl = str(shareurl or "").strip()
        if not shareurl:
            return self._lead_in_error("LEAD_IN_FAILED", "请先填写素材分享链接")
        try:
            pwd_id, url_passcode, pdir_fid, paths = self.extract_url(shareurl)
        except Exception as exc:
            logger.error("[Quark] 解析引流素材链接失败: %s", exc)
            return self._lead_in_error("LEAD_IN_FAILED", f"解析素材分享链接失败：{exc}")
        if not pwd_id:
            return self._lead_in_error(
                "LEAD_IN_MATERIAL_UNAVAILABLE",
                "无法从分享链接中识别夸克分享标识，请检查链接是否为夸克分享地址",
            )
        use_passcode = str(passcode or "").strip() or str(url_passcode or "").strip()
        stoken, error = self._lead_in_quark_stoken(pwd_id, use_passcode)
        if error:
            return error
        # 深层链接（/s/xxx/fid-name）下的相对路径，用于拼素材完整路径
        base_path = ""
        path_names = [
            str(item.get("name") or "").strip()
            for item in (paths or [])
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        ]
        if path_names:
            base_path = "/" + "/".join(path_names)
        root_fid = str(pdir_fid or "0")
        try:
            detail = self.get_detail(pwd_id, stoken, root_fid)
        except Exception as exc:
            logger.error("[Quark] 读取引流素材分享列表失败: %s", exc)
            return self._lead_in_error("LEAD_IN_MATERIAL_UNAVAILABLE", f"读取分享文件列表失败：{exc}")
        if not isinstance(detail, dict) or detail.get("code") not in (0, "0"):
            message = str((detail or {}).get("message") or "").strip() if isinstance(detail, dict) else ""
            return self._lead_in_error(
                "LEAD_IN_MATERIAL_UNAVAILABLE",
                f"读取分享文件列表失败：{message or '分享链接可能已失效'}",
            )
        raw_items = ((detail.get("data") or {}).get("list")) or []
        entries = [
            self._lead_in_entry(item, base_path)
            for item in raw_items
            if isinstance(item, dict)
        ]
        entries = [entry for entry in entries if entry.get("fid") and entry.get("name")]
        if not entries:
            return self._lead_in_error(
                "LEAD_IN_MATERIAL_UNAVAILABLE",
                "分享内没有可用文件，请检查分享链接是否有效或是否需要提取码",
            )
        entry, error = self._lead_in_pick_entry(entries, selected_fid)
        if error:
            # 分享内不唯一时把文件列表一并返回，前端据此让用户选择素材
            if error.get("code") == "LEAD_IN_MATERIAL_AMBIGUOUS":
                error["data"] = {
                    "files": self._lead_in_public_files(entries),
                    "pwd_id": pwd_id,
                    "stoken": stoken,
                }
            return error
        material = self._lead_in_build_material(
            entry,
            pwd_id=pwd_id,
            stoken=stoken,
            shareurl=shareurl,
            passcode=use_passcode,
        )
        return self._lead_in_ok(
            {
                "files": self._lead_in_public_files(entries),
                "material": material,
                "pwd_id": pwd_id,
                "stoken": stoken,
            }
        )

    # 夸克分享被取消/失效时返回的错误码（与 _lead_in_quark_stoken 共用）
    SHARE_INVALID_CODES = (41004, 41005, 41009, 41012)

    def classify_share_failure(self, response: Dict[str, Any]) -> Dict[str, str]:
        """把夸克分享校验失败响应归类（生成分享链接检测、任务引流共用）。"""
        data = response if isinstance(response, dict) else {}
        code = self._lead_in_to_int(data.get("code"), 0)
        message = str(data.get("message") or "").strip()
        if self._lead_in_quark_passcode_issue(data):
            return {"kind": "passcode", "message": message or "该分享需要提取码"}
        if any(word in message for word in ("登录", "认证", "Cookie", "cookie", "账号")):
            return {"kind": "auth", "message": message or "账号登录失败"}
        if code in self.SHARE_INVALID_CODES or any(
            word in message
            for word in ("不存在", "已失效", "失效", "取消", "过期", "违规", "删除")
        ):
            return {"kind": "invalid", "message": message or "分享链接不存在或已失效"}
        if "限流" in message or "频率过高" in message or str(data.get("status")) == "429":
            return {"kind": "throttled", "message": message or "请求频率过高，请稍后重试"}
        return {"kind": "unknown", "message": message}

    def _lead_in_quark_stoken(
        self, pwd_id: str, passcode: str
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """获取夸克分享令牌并把错误映射成引流错误码"""
        try:
            response = self.get_stoken(pwd_id, passcode)
        except Exception as exc:
            logger.error("[Quark] 获取引流素材分享令牌失败: %s", exc)
            return None, self._lead_in_error("LEAD_IN_FAILED", f"获取分享令牌失败：{exc}")
        if not isinstance(response, dict):
            return None, self._lead_in_error("LEAD_IN_FAILED", "获取分享令牌失败：网盘返回异常")
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        stoken = str(data.get("stoken") or "").strip()
        code = response.get("code")
        status = response.get("status")
        if stoken and code in (None, 0, "0") and status in (None, 200, "200"):
            return stoken, None
        message = str(response.get("message") or "").strip()
        verdict = self.classify_share_failure(response)
        kind = str(verdict.get("kind") or "unknown")
        if kind == "passcode":
            if not passcode:
                return None, self._lead_in_error(
                    "LEAD_IN_PASSCODE_REQUIRED",
                    "该分享需要提取码，请填写提取码后重试",
                )
            return None, self._lead_in_error(
                "LEAD_IN_PASSCODE_INVALID",
                f"提取码错误，请检查后重试（{message or '请确认提取码'}）",
            )
        if kind == "invalid":
            return None, self._lead_in_error(
                "LEAD_IN_MATERIAL_UNAVAILABLE",
                f"素材分享链接不可用：{message or '链接不存在或已失效'}",
            )
        return None, self._lead_in_error(
            "LEAD_IN_FAILED",
            f"获取分享令牌失败：{message or '未知原因'}",
        )

    @staticmethod
    def _lead_in_quark_passcode_issue(response: Dict[str, Any]) -> bool:
        """判断夸克响应是否是提取码问题"""
        if not isinstance(response, dict):
            return False
        message = str(response.get("message") or "")
        lowered = message.lower()
        if "提取码" in message or "密码" in message or "口令" in message:
            return True
        if "passcode" in lowered or "password" in lowered:
            return True
        return response.get("code") in (41006, 41008)

    @classmethod
    def _lead_in_pick_quark_entry(
        cls, items: Any, name: str
    ) -> Optional[Dict[str, Any]]:
        """在目录列表里挑选目标项：先精确匹配名称，再容忍首尾空白"""
        tolerant: Optional[Dict[str, Any]] = None
        for item in items or []:
            if not isinstance(item, dict):
                continue
            item_name = cls._lead_in_item_dir_name(item)
            if item_name == name:
                return item
            if tolerant is None and cls._lead_in_dir_name_matches(item_name, name):
                tolerant = item
        return tolerant

    def _lead_in_locate_dir_chain(
        self, path: str
    ) -> Tuple[Optional[Dict[str, Any]], str, Optional[Dict[str, Any]]]:
        """逐级枚举目录定位目标文件夹（精确路径匹配失败时兜底，容忍首尾空白）

        返回 (目录项, 网盘真实路径, 错误)；真实路径用于后续按 fid 读写时
        回填进目录描述，避免把用户手输的路径当作真实路径。
        """
        segments = self._lead_in_path_segments(path)
        fid = "0"
        current = "/"
        folder: Optional[Dict[str, Any]] = None
        for index, segment in enumerate(segments):
            try:
                listing = self.ls_dir(fid, max_items=0)
            except Exception as exc:
                logger.error("[Quark] 逐级定位引流目标目录失败: %s", exc)
                return None, "", self._lead_in_error(
                    "LEAD_IN_FAILED", f"定位保存目录失败：{exc}"
                )
            raw_items = None
            if isinstance(listing, dict):
                data = listing.get("data")
                if isinstance(data, dict) and isinstance(data.get("list"), list):
                    raw_items = data.get("list")
            if not isinstance(raw_items, list):
                return None, "", self._lead_in_error(
                    "LEAD_IN_DIR_NOT_FOUND", f"保存目录不存在：{path}"
                )
            folder = self._lead_in_pick_quark_entry(raw_items, segment)
            if folder is None:
                return None, "", self._lead_in_error(
                    "LEAD_IN_DIR_NOT_FOUND", f"保存目录不存在：{path}"
                )
            if (
                folder.get("dir") is False
                or folder.get("is_dir") is False
                or str(folder.get("file_type") or "") == "1"
            ):
                if index == len(segments) - 1:
                    return None, "", self._lead_in_error(
                        "LEAD_IN_DIR_INVALID", f"保存路径不是文件夹：{path}"
                    )
                return None, "", self._lead_in_error(
                    "LEAD_IN_DIR_NOT_FOUND", f"保存目录不存在：{path}"
                )
            fid = str(folder.get("fid") or "").strip()
            if not fid:
                return None, "", self._lead_in_error(
                    "LEAD_IN_DIR_INVALID", f"保存目录无法精确定位：{path}"
                )
            item_name = self._lead_in_item_dir_name(folder)
            current = f"{current.rstrip('/')}/{item_name}" if current != "/" else f"/{item_name}"
        return folder, current or "/", None

    def lead_in_resolve_dir(self, dir_path: str) -> Dict[str, Any]:
        """按任务保存目录定位夸克目标文件夹

        任务保存路径可能是手输的，网盘上的真实目录名可能与它只差首尾空格；
        精确路径匹配失败时逐级枚举目录兜底定位，并用真实路径继续后续操作。
        """
        path, error = self._lead_in_normalize_dir_path(dir_path)
        if error:
            return error
        try:
            candidates = self.get_fids([path]) or []
        except Exception as exc:
            logger.error("[Quark] 定位引流目标目录失败: %s", exc)
            return self._lead_in_error("LEAD_IN_FAILED", f"定位保存目录失败：{exc}")
        folder = None
        for item in candidates:
            if not isinstance(item, dict):
                continue
            file_path = str(item.get("file_path") or item.get("path") or "").rstrip("/")
            if file_path == path:
                folder = item
                break
        if folder is None:
            folder, chain_path, chain_error = self._lead_in_locate_dir_chain(path)
            if chain_error:
                return chain_error
            path = chain_path or path
        if folder is None:
            return self._lead_in_error("LEAD_IN_DIR_NOT_FOUND", f"保存目录不存在：{path}")
        fid = str(folder.get("fid") or "").strip()
        if not fid:
            return self._lead_in_error("LEAD_IN_DIR_INVALID", f"保存目录无法精确定位：{path}")
        if folder.get("dir") is False or folder.get("is_dir") is False or str(folder.get("file_type") or "") == "1":
            return self._lead_in_error("LEAD_IN_DIR_INVALID", f"保存路径不是文件夹：{path}")
        try:
            listing = self.ls_dir(fid, max_items=0)
        except Exception as exc:
            logger.error("[Quark] 读取引流目标目录失败: %s", exc)
            return self._lead_in_error("LEAD_IN_FAILED", f"读取保存目录失败：{exc}")
        raw_items = None
        if isinstance(listing, dict):
            data = listing.get("data")
            if isinstance(data, dict) and isinstance(data.get("list"), list):
                raw_items = data.get("list")
        if not isinstance(raw_items, list):
            message = str((listing or {}).get("message") or "").strip() if isinstance(listing, dict) else ""
            return self._lead_in_error(
                "LEAD_IN_DIR_INVALID",
                f"保存路径不是文件夹或无法访问：{path}" + (f"（{message}）" if message else ""),
            )
        entries = [
            self._lead_in_public_entry(self._lead_in_entry(item))
            for item in raw_items
            if isinstance(item, dict)
        ]
        name = path.rsplit("/", 1)[-1]
        return self._lead_in_ok(
            {
                "dir": {"fid": fid, "path": path, "name": name},
                "entries": entries,
            }
        )

    def lead_in_add(
        self,
        material: Dict[str, Any],
        target: Dict[str, Any],
    ) -> Dict[str, Any]:
        """只提交选中的那一个素材文件转存（夸克转存为异步任务）"""
        data, error = self._lead_in_prepare_material(material)
        if error:
            return error
        fid = str(data.get("fid") or "").strip()
        name = str(data.get("name") or "").strip()
        token = str(data.get("material_token") or "").strip() or fid
        dir_info, error = self._lead_in_target_dir(target)
        if error:
            return error
        if not fid:
            return self._lead_in_error("LEAD_IN_MATERIAL_UNAVAILABLE", "素材文件信息不完整，请重新解析素材")
        dir_fid = str(dir_info.get("fid") or "").strip()
        pwd_id = str(data.get("pwd_id") or "").strip()
        stoken = str(data.get("stoken") or "").strip()
        try:
            result = self.save_file([fid], [token], dir_fid, pwd_id, stoken)
        except Exception as exc:
            logger.error("[Quark] 引流素材转存请求异常: %s", exc)
            return self._lead_in_error(
                "LEAD_IN_TIMEOUT",
                f"转存请求异常，添加结果待确认：{exc}",
            )
        if not isinstance(result, dict):
            return self._lead_in_error("LEAD_IN_FAILED", "转存接口返回异常，请稍后重试")
        code = result.get("code")
        status = result.get("status")
        if code not in (0, "0") or status not in (None, 200, "200"):
            message = str(result.get("message") or "").strip()
            if any(word in message for word in ("已存在", "同名", "重复")):
                return self._lead_in_error(
                    "LEAD_IN_NAME_CONFLICT",
                    f"目标目录已存在同名文件，未覆盖：{message}",
                )
            if code in (41004, 41005, 41009) or any(
                word in message for word in ("分享", "提取码", "失效", "不存在")
            ):
                return self._lead_in_error(
                    "LEAD_IN_MATERIAL_UNAVAILABLE",
                    f"素材分享不可用：{message or '请重新解析素材'}",
                )
            return self._lead_in_error("LEAD_IN_FAILED", f"转存失败：{message or '未知原因'}")
        result_data = result.get("data") if isinstance(result.get("data"), dict) else {}
        task_id = str(result_data.get("task_id") or result_data.get("taskId") or "").strip()
        if not task_id:
            return self._lead_in_error("LEAD_IN_FAILED", "转存任务提交失败（未返回任务 ID），请稍后重试")
        handle = {
            "task_id": task_id,
            "dir_fid": dir_fid,
            "fid": fid,
            "name": name,
            "size": self._lead_in_to_int(data.get("size")),
            "md5": str(data.get("md5") or ""),
            "pwd_id": pwd_id,
            "stoken": stoken,
        }
        return self._lead_in_ok({"handle": handle, "sync": False})

    def _lead_in_query_task_once(self, task_id: str) -> Dict[str, Any]:
        """单次查询夸克转存任务（核验阶段不能用无界的 query_task）"""
        import random
        import time as _time
        from datetime import datetime

        url = f"{self.BASE_URL}/1/clouddrive/task"
        querystring = {
            "pr": "ucpro",
            "fr": "pc",
            "uc_param_str": "",
            "task_id": task_id,
            "retry_index": 0,
            "__dt": int(random.uniform(1, 5) * 60 * 1000),
            "__t": datetime.now().timestamp(),
        }
        return self._send_request("GET", url, params=querystring).json()

    def _lead_in_query_write_state(self, handle: Dict[str, Any]) -> Tuple[str, str]:
        """查询夸克转存任务状态（done/running/failed）"""
        task_id = str((handle or {}).get("task_id") or "").strip()
        if not task_id:
            return "unknown", ""
        try:
            response = self._lead_in_query_task_once(task_id)
        except Exception as exc:
            logger.warning("[Quark] 查询引流转存任务失败: %s", exc)
            return "unknown", str(exc)
        if not isinstance(response, dict):
            return "unknown", ""
        if response.get("status") not in (None, 200, "200"):
            message = str(response.get("message") or "").strip()
            if any(word in message for word in ("不存在", "失效", "过期")):
                return "failed", f"转存任务已失效：{message}"
            return "unknown", message
        data = response.get("data") if isinstance(response.get("data"), dict) else {}
        task_status = self._lead_in_to_int(data.get("status"), -1)
        if task_status == 2:
            return "done", ""
        if task_status in (3, 4, -1):
            message = str(data.get("message") or data.get("task_title") or "").strip()
            return "failed", f"网盘转存任务失败：{message or '未知原因'}"
        fail_code = data.get("fail_code") or data.get("code")
        if fail_code not in (None, 0, "0"):
            return "failed", f"网盘转存任务失败（code={fail_code}）"
        # 任务仍在进行中（status 0/1 等），交给调用方继续有界轮询
        return "running", ""

    # ==================== 任务引流（本账号文件） ====================

    def lead_in_resolve_own_file(self, file_path: str) -> Dict[str, Any]:
        """在本账号内按完整路径精确定位引流素材文件（只按精确路径，不按文件名搜索）"""
        path, error = self._lead_in_normalize_own_path(file_path)
        if error:
            return error
        name = path.rsplit("/", 1)[-1]
        # 夸克 path_list 对文件返回空列表（只稳定支持目录），不能先用它校验文件；
        # 因此先按完整路径定位父目录 fid，再在父目录的精确列表中按同名文件定位（不做全盘搜索）。
        parent_path = path.rsplit("/", 1)[0] or "/"
        parent_fid = "0"
        if parent_path != "/":
            try:
                parents = self.get_fids([parent_path]) or []
            except Exception as exc:
                logger.error("[Quark] 定位素材所在文件夹失败: %s", exc)
                return self._lead_in_error("LEAD_IN_FAILED", f"定位素材所在文件夹失败：{exc}")
            parent_item = None
            for item in parents:
                if not isinstance(item, dict):
                    continue
                if self._lead_in_quark_normalize_path(
                    item.get("file_path") or item.get("path")
                ) == parent_path:
                    parent_item = item
                    break
            if parent_item is None or not str(parent_item.get("fid") or "").strip():
                return self._lead_in_error(
                    "LEAD_IN_MATERIAL_UNAVAILABLE",
                    f"素材文件不存在，请检查路径：{path}",
                )
            parent_fid = str(parent_item.get("fid")).strip()
        try:
            listing = self.ls_dir(parent_fid, max_items=2000)
        except Exception as exc:
            logger.error("[Quark] 读取素材所在文件夹失败: %s", exc)
            return self._lead_in_error("LEAD_IN_FAILED", f"读取素材所在文件夹失败：{exc}")
        raw_items = None
        if isinstance(listing, dict):
            if listing.get("code") not in (0, "0", None):
                message = str(listing.get("message") or "").strip()
                return self._lead_in_error(
                    "LEAD_IN_FAILED",
                    "读取素材所在文件夹失败" + (f"：{message}" if message else ""),
                )
            data = listing.get("data")
            if isinstance(data, dict) and isinstance(data.get("list"), list):
                raw_items = data.get("list")
        if raw_items is None:
            message = (
                str(listing.get("message") or "").strip()
                if isinstance(listing, dict)
                else ""
            )
            return self._lead_in_error(
                "LEAD_IN_FAILED",
                "读取素材所在文件夹失败" + (f"：{message}" if message else ""),
            )
        entry = None
        folder_entry = None
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            entry_item = self._lead_in_entry(item)
            if entry_item.get("name") != name:
                continue
            if entry_item.get("dir"):
                folder_entry = entry_item
            elif entry is None:
                entry = entry_item
        if entry is None and folder_entry is not None:
            return self._lead_in_error(
                "LEAD_IN_MATERIAL_UNAVAILABLE", "只能选择文件，不能选择文件夹"
            )
        if entry is None:
            return self._lead_in_error(
                "LEAD_IN_MATERIAL_UNAVAILABLE", f"素材文件不存在，请检查路径：{path}"
            )
        fid = str(entry.get("fid") or "").strip()
        if not fid:
            return self._lead_in_error(
                "LEAD_IN_MATERIAL_UNAVAILABLE", f"素材文件不存在，请检查路径：{path}"
            )
        md5 = str(entry.get("md5") or "").strip().lower()
        return self._lead_in_ok(
            {
                "file": {
                    "fid": fid,
                    "name": name,
                    "path": path,
                    "size": self._lead_in_to_int(entry.get("size")),
                    "md5": md5,
                    "dir": False,
                }
            }
        )

    @staticmethod
    def _lead_in_quark_normalize_path(raw_path: Any) -> str:
        """归一夸克返回的路径，便于与用户输入的完整路径做精确比较"""
        return "/" + "/".join(
            part for part in str(raw_path or "").replace("\\", "/").split("/") if part
        )

    def lead_in_add_own_file(
        self,
        material: Dict[str, Any],
        target: Dict[str, Any],
    ) -> Dict[str, Any]:
        """把本账号内的单个素材文件内部复制到目标目录（夸克为异步任务）"""
        material = material if isinstance(material, dict) else {}
        dir_info, error = self._lead_in_target_dir(target)
        if error:
            return error
        dir_fid = str(dir_info.get("fid") or "").strip()
        fid = str(material.get("fid") or "").strip()
        name = str(material.get("name") or "").strip()
        if not fid:
            path, path_error = self._lead_in_normalize_own_path(
                str(material.get("path") or "")
            )
            if path_error:
                return path_error
            resolved = self.lead_in_resolve_own_file(path)
            if resolved.get("code") != 0:
                return resolved
            file_info = (resolved.get("data") or {}).get("file") or {}
            fid = str(file_info.get("fid") or "").strip()
            name = name or str(file_info.get("name") or "").strip()
            if not fid:
                return self._lead_in_error(
                    "LEAD_IN_MATERIAL_UNAVAILABLE", "素材文件信息不完整，请重新读取文件"
                )
        if not name:
            name = str(material.get("path") or "").rstrip("/").rsplit("/", 1)[-1]
        url = f"{self.BASE_URL}/1/clouddrive/file/copy"
        querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        payload = {"filelist": [fid], "to_pdir_fid": dir_fid}
        try:
            response = self._send_request(
                "POST", url, json=payload, params=querystring
            ).json()
        except Exception as exc:
            logger.error("[Quark] 本账号素材复制请求异常: %s", exc)
            return self._lead_in_error(
                "LEAD_IN_TIMEOUT", f"复制请求异常，添加结果待确认：{exc}"
            )
        if not isinstance(response, dict):
            return self._lead_in_error("LEAD_IN_FAILED", "复制接口返回异常，请稍后重试")
        code = response.get("code")
        if code not in (0, "0"):
            message = str(response.get("message") or "").strip()
            mapped = self._lead_in_copy_error(message, code=code)
            if mapped:
                return mapped
            return self._lead_in_error(
                "LEAD_IN_FAILED", f"复制素材失败：{message or '未知原因'}"
            )
        result_data = response.get("data") if isinstance(response.get("data"), dict) else {}
        task_id = str(result_data.get("task_id") or result_data.get("taskId") or "").strip()
        handle = {
            "source": "own_file",
            "task_id": task_id,
            "dir_fid": dir_fid,
            "fid": fid,
            "name": name,
            "size": self._lead_in_to_int(material.get("size")),
            "md5": str(material.get("md5") or "").strip().lower(),
        }
        return self._lead_in_ok({"handle": handle, "sync": False})

    # 以下为夸克特有方法，不在基类接口中

    def sign_in(self) -> Dict[str, Any]:
        growth_info = self.get_growth_info()
        if growth_info:
            if growth_info["cap_sign"]["sign_daily"]:
                tmp = (
                    f"签到日志: 今日已签到+{self.convert_bytes(growth_info['cap_sign']['sign_daily_reward'])}，"
                    f"连签进度({growth_info['cap_sign']['sign_progress']}/{growth_info['cap_sign']['sign_target']})"
                )
                return {"supported": True, "ok": True, "message": tmp}
            else:
                sign, sign_return = self.get_growth_sign()
                if sign:
                    tmp = (
                        f"执行签到: 今日签到+{self.convert_bytes(sign_return)}，"
                        f"连签进度({growth_info['cap_sign']['sign_progress'] + 1}/{growth_info['cap_sign']['sign_target']})"
                    )
                    return {"supported": True, "ok": bool(sign), "message": tmp}
                else:
                    return {"supported": True, "ok": bool(sign), "message": sign_return}
        else:
            return {"supported": True, "ok": False, "message": "获取成长信息失败"}

    def get_growth_info(self) -> Any:
        """获取成长信息（签到用）"""
        url = f"{self.BASE_URL_APP}/1/clouddrive/capacity/growth/info"
        querystring = {
            "pr": "ucpro",
            "fr": "android",
            "kps": self.mparam.get("kps"),
            "sign": self.mparam.get("sign"),
            "vcode": self.mparam.get("vcode"),
        }
        headers = {
            "content-type": "application/json",
        }
        response = self._send_request(
            "GET", url, headers=headers, params=querystring
        ).json()
        if response.get("data"):
            return response["data"]
        else:
            return False

    def get_growth_sign(self) -> Tuple[bool, Any]:
        """执行签到"""
        url = f"{self.BASE_URL_APP}/1/clouddrive/capacity/growth/sign"
        querystring = {
            "pr": "ucpro",
            "fr": "android",
            "kps": self.mparam.get("kps"),
            "sign": self.mparam.get("sign"),
            "vcode": self.mparam.get("vcode"),
        }
        payload = {
            "sign_cyclic": True,
        }
        headers = {
            "content-type": "application/json",
        }
        response = self._send_request(
            "POST", url, json=payload, headers=headers, params=querystring
        ).json()
        if response.get("data"):
            return True, response["data"]["sign_daily_reward"]
        else:
            return False, response["message"]

    def recycle_list(self, page: int = 1, size: int = 30) -> List:
        """获取回收站列表"""
        url = f"{self.BASE_URL}/1/clouddrive/file/recycle/list"
        querystring = {
            "_page": page,
            "_size": size,
            "pr": "ucpro",
            "fr": "pc",
            "uc_param_str": "",
        }
        response = self._send_request("GET", url, params=querystring).json()
        return response["data"]["list"]

    def recycle_remove(self, record_list: List) -> Dict:
        """清空回收站"""
        url = f"{self.BASE_URL}/1/clouddrive/file/recycle/remove"
        querystring = {"uc_param_str": "", "fr": "pc", "pr": "ucpro"}
        payload = {
            "select_mode": 2,
            "record_list": record_list,
        }
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    def unarchive(self, fid, to_pdir_fid):
        url = f"{self.BASE_URL}/1/clouddrive/archive/unarchive"
        querystring = {"uc_param_str": "", "fr": "pc", "pr": "ucpro"}
        payload = {
            "fid": fid,
            "to_pdir_fid": to_pdir_fid,
            "conflict_mode": 3,
            "suffix_type": 0,
            "pwd": "",
            "select_mode": 0,
        }
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    def move_files(self, fids, to_pdir_fid):
        url = f"{self.BASE_URL}/1/clouddrive/file/move"
        querystring = {"uc_param_str": "", "fr": "pc", "pr": "ucpro"}
        payload = {
            "filelist": fids,
            "to_pdir_fid": to_pdir_fid,
            "exclude_fids": [],
            "action_type": 1,
        }
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    def download(self, fids: List[str]) -> Tuple[Dict, str]:
        """获取下载链接"""
        url = f"{self.BASE_URL}/1/clouddrive/file/download"
        querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        payload = {"fids": fids}
        response = self._send_request("POST", url, json=payload, params=querystring)
        set_cookie = response.cookies.get_dict()
        cookie_str = "; ".join([f"{key}={value}" for key, value in set_cookie.items()])
        return response.json(), cookie_str
