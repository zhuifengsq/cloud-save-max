from __future__ import annotations

import logging
import re
import threading
from typing import Any

import requests
from cachetools import TTLCache
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.extensions.runtime.adapter_registry import AdapterRegistry
from app.models.drive_account import DriveAccount
from app.services.invalid_share_links import list_invalid_shareurls


logger = logging.getLogger(__name__)

_search_cache_ttl = 3
_search_cache: TTLCache[str, tuple[list[dict[str, Any]], bool, str | None]] = TTLCache(maxsize=500, ttl=_search_cache_ttl)
_search_cache_lock = threading.Lock()


def _get_search_cache_ttl() -> int:
    try:
        from app.models.system_setting import SystemSetting
        from app.db.session import SessionLocal
        with SessionLocal() as db:
            row = db.query(SystemSetting).filter(SystemSetting.key == "search_cache_ttl_seconds").first()
            if row and row.value:
                return max(1, min(3600, int(row.value)))
    except Exception:
        pass
    return 3


def _update_search_cache_ttl() -> None:
    global _search_cache, _search_cache_ttl
    ttl = _get_search_cache_ttl()
    if ttl != _search_cache_ttl:
        _search_cache_ttl = ttl
        _search_cache = TTLCache(maxsize=500, ttl=ttl)


def cache_clear() -> None:
    with _search_cache_lock:
        _search_cache.clear()


def _normalize_drive_type(value: str | None) -> str | None:
    v = str(value or "").strip()
    if not v:
        return None
    k = v.lower()
    mapping = {
        "pan115": "115",
        "cloud115": "115",
        "115": "115",
        "pan123": "123pan",
        "123pan": "123pan",
        "tianyi": "cloud189",
        "cloud189": "cloud189",
        "baidupan": "baidu",
        "baidu": "baidu",
        "quark": "quark",
        "uc": "uc",
        "aliyun": "aliyun",
        "xunlei": "xunlei",
    }
    return mapping.get(k) or k


def extract_quark_url(url: str) -> tuple[str | None, str, str | None]:
    """解析夸克分享链接，提取 pwd_id、passcode 和分享者信息"""
    if not url:
        return None, "", None
    
    # 提取 pwd_id
    match_id = re.search(r"/s/(\w+)", url)
    pwd_id = match_id.group(1) if match_id else None
    
    # 提取 passcode
    match_pwd = re.search(r"pwd=(\w+)", url)
    passcode = match_pwd.group(1) if match_pwd else ""
    
    return pwd_id, passcode, None


def get_quark_share_author(shareurl: str) -> str | None:
    """获取夸克分享链接的分享者昵称"""
    try:
        pwd_id, passcode, _ = extract_quark_url(shareurl)
        if not pwd_id:
            return None
        
        # 获取夸克账号
        from app.db.session import get_db_session
        with get_db_session() as db:
            accounts = db.execute(
                select(DriveAccount).where(
                    DriveAccount.drive_type == "quark",
                    DriveAccount.enabled == True,
                    DriveAccount.runtime_status == "active"
                )
            ).scalars().all()
            
            if not accounts:
                return None
            
            # 使用第一个可用账号
            account = accounts[0]
            cookie = account.cookie or ""
            if not cookie:
                return None
            
            # 解析 cookie 获取移动端参数
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
            
            # 发送请求获取分享者信息
            base_url = "https://drive-pc.quark.cn"
            base_url_app = "https://drive-m.quark.cn"
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) quark-cloud-drive/3.14.2 Chrome/112.0.5615.165 Electron/24.1.3.8 Safari/537.36 Channel/pckk_other_ch"
            
            url = f"{base_url}/1/clouddrive/share/sharepage/token"
            querystring = {"pr": "ucpro", "fr": "pc"}
            payload = {"pwd_id": pwd_id, "passcode": passcode}
            
            headers = {
                "cookie": cookie,
                "content-type": "application/json",
                "user-agent": user_agent,
            }
            
            # 如果有移动端参数，使用移动端接口
            if mparam:
                url = url.replace(base_url, base_url_app)
                querystring.update({
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
                    "kps": mparam.get("kps"),
                    "sign": mparam.get("sign"),
                    "vcode": mparam.get("vcode"),
                    "app": "clouddrive",
                    "kkkk": "1",
                })
                del headers["cookie"]
            
            response = requests.post(url, json=payload, params=querystring, headers=headers, timeout=15)
            data = response.json()
            
            if data.get("status") == 200:
                author = data.get("data", {}).get("author")
                if author and isinstance(author, dict):
                    return (author.get("nick_name") or 
                            author.get("nickname") or 
                            author.get("user_name") or "").strip()
            
            return None
            
    except Exception as e:
        logger.warning(f"获取夸克分享者信息失败: {e}")
        return None


def enrich_suggestions_with_authors(suggestions: list[dict[str, Any]], db: Session = None) -> list[dict[str, Any]]:
    """为搜索结果添加分享者信息（仅夸克网盘）"""
    from app.models.system_setting import SystemSetting
    
    # 获取优选分享者列表
    preferred_sharers = []
    if db:
        preferred_setting = db.query(SystemSetting).filter(SystemSetting.key == "preferred_sharers").first()
        if preferred_setting and preferred_setting.value.strip():
            preferred_sharers = [
                name.strip() 
                for name in preferred_setting.value.split("|") 
                if name.strip()
            ]
    
    for item in suggestions:
        shareurl = str(item.get("shareurl") or "").strip()
        if not shareurl:
            continue
        
        # 检测网盘类型
        dt = AdapterRegistry.detect_drive_type(shareurl)
        if dt != "quark":
            continue
        
        # 获取分享者信息
        author_name = get_quark_share_author(shareurl)
        if author_name:
            item["share_author_name"] = author_name
            # 判断是否为优选分享者
            if author_name in preferred_sharers:
                item["is_preferred_sharer"] = True
    
    return suggestions


def filter_blocked_sharers(db: Session, suggestions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """过滤屏蔽分享者的结果"""
    from app.models.system_setting import SystemSetting
    
    # 获取屏蔽分享者列表
    blocked_setting = db.query(SystemSetting).filter(SystemSetting.key == "blocked_sharers").first()
    if not blocked_setting or not blocked_setting.value.strip():
        return suggestions
    
    blocked_sharers = [
        name.strip() 
        for name in blocked_setting.value.split("|") 
        if name.strip()
    ]
    
    if not blocked_sharers:
        return suggestions
    
    # 过滤掉匹配屏蔽列表的结果
    filtered = []
    for item in suggestions:
        author_name = str(item.get("share_author_name") or "").strip()
        if not author_name or author_name not in blocked_sharers:
            filtered.append(item)
    
    return filtered


def fetch_task_suggestions(
    db: Session,
    keyword: str,
    deep: int,
    *,
    filter_invalid: bool = True,
    drive_type: str | None = None,
    search_filter: str = "",
    search_exclude: str = "",
    search_date_from: str = "",
    search_filter_mode: str = "",
    search_exclude_mode: str = "",
) -> tuple[list[dict[str, Any]], bool, str | None]:
    keyword = str(keyword or "").strip()
    if len(keyword) < 2:
        return ([], False, None)
    deep = deep or 0
    dt_filter = _normalize_drive_type(drive_type)
    filter_keywords = [kw.strip().lower() for kw in str(search_filter or "").split("|") if kw.strip()]
    exclude_keywords = [kw.strip().lower() for kw in str(search_exclude or "").split("|") if kw.strip()]
    date_from = str(search_date_from or "").strip()
    filter_mode = str(search_filter_mode or "all").strip().lower()  # all / any
    exclude_mode = str(search_exclude_mode or "any").strip().lower()  # all / any

    # 缓存查找
    cache_key = f"{keyword}|{deep}|{dt_filter}|{search_filter}|{search_exclude}|{date_from}|{filter_mode}|{exclude_mode}"
    _update_search_cache_ttl()
    with _search_cache_lock:
        cached = _search_cache.get(cache_key)
        if cached is not None:
            return cached

    from app.services.api_search_sources import search_enabled_api_sources
    from app.services.search_plugins import search_enabled_plugins

    search_results: list[dict[str, Any]] = []
    for search in (search_enabled_api_sources, search_enabled_plugins):
        try:
            items = search(db, keyword)
            if isinstance(items, list):
                search_results.extend(item for item in items if isinstance(item, dict))
        except Exception:
            logger.exception("搜索源执行失败")

    results: list[dict[str, Any]] = []
    link_set: set[str] = set()

    def _sort_key(x: dict[str, Any]) -> str:
        v = x.get("datetime")
        return str(v or "")

    search_results.sort(key=_sort_key, reverse=True)
    for item in search_results:
        url = str(item.get("shareurl") or "").strip()
        if not url or url in link_set:
            continue
        link_set.add(url)
        results.append(item)

    enabled_drive_types = set(
        str(x or "").strip()
        for x in db.execute(
            select(DriveAccount.drive_type).where(DriveAccount.enabled.is_(True), DriveAccount.runtime_status == "active")
        )
        .scalars()
        .all()
    )
    enabled_drive_types = {_normalize_drive_type(x) for x in enabled_drive_types}
    enabled_drive_types = {x for x in enabled_drive_types if x}
    if not enabled_drive_types:
        return ([], False, "没有可用的网盘账号（enabled=true 且 runtime_status=active），已隐藏不支持的资源")
    if dt_filter and dt_filter not in enabled_drive_types:
        return ([], False, f"指定网盘类型没有可用账号：{dt_filter}")

    filtered: list[dict[str, Any]] = []
    removed_by_accounts = 0
    removed_by_drive_type = 0
    for item in results:
        url = str(item.get("shareurl") or "").strip()
        if not url:
            continue
        dt = AdapterRegistry.detect_drive_type(url)
        if not dt or str(dt) not in enabled_drive_types:
            removed_by_accounts += 1
            continue
        if dt_filter and str(dt) != dt_filter:
            removed_by_drive_type += 1
            continue
        name = item.get("taskname") if item.get("taskname") else item.get("content", "")
        item['taskname'] = name
        filtered.append(item)

    message = None
    msg_parts: list[str] = []
    if dt_filter:
        msg_parts.append(f"已限定网盘类型：{dt_filter}")
    elif removed_by_accounts:
        msg_parts.append(f"已按可用网盘账号过滤：{', '.join(sorted(enabled_drive_types))}")
    if msg_parts:
        message = "; ".join(msg_parts)

    if filter_invalid:
        invalid = list_invalid_shareurls(db, shareurls=[str(x.get("shareurl") or "").strip() for x in filtered])
        if invalid:
            filtered = [x for x in filtered if str(x.get("shareurl") or "").strip() not in invalid]
            extra = f"已过滤失效链接：{len(invalid)}"
            message = f"{message}; {extra}" if message else extra

    # 搜索筛选词过滤
    if filter_keywords:
        before_count = len(filtered)
        if filter_mode == "any":
            filtered = [
                item for item in filtered
                if any(kw in str(item.get("taskname") or "").lower() for kw in filter_keywords)
            ]
        else:
            filtered = [
                item for item in filtered
                if all(kw in str(item.get("taskname") or "").lower() for kw in filter_keywords)
            ]
        removed = before_count - len(filtered)
        if removed:
            extra = f"筛选词过滤：{removed} 条"
            message = f"{message}; {extra}" if message else extra

    # 搜索排除词过滤
    if exclude_keywords:
        before_count = len(filtered)
        if exclude_mode == "all":
            filtered = [
                item for item in filtered
                if not all(kw in str(item.get("taskname") or "").lower() for kw in exclude_keywords)
            ]
        else:
            filtered = [
                item for item in filtered
                if not any(kw in str(item.get("taskname") or "").lower() for kw in exclude_keywords)
            ]
        removed = before_count - len(filtered)
        if removed:
            extra = f"排除词过滤：{removed} 条"
            message = f"{message}; {extra}" if message else extra

    # 时间过滤：过滤掉早于指定日期的搜索结果（没有时间的条目保留）
    if date_from:
        before_count = len(filtered)
        filtered = [
            item for item in filtered
            if not str(item.get("datetime") or "").strip() or str(item.get("datetime") or "").strip() >= date_from
        ]
        removed = before_count - len(filtered)
        if removed:
            extra = f"时间过滤：{removed} 条"
            message = f"{message}; {extra}" if message else extra

    result = (filtered, False, message)
    with _search_cache_lock:
        _search_cache[cache_key] = result
    return result
