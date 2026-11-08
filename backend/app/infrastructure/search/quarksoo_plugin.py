# -*- coding: utf-8 -*-
"""QuarkSoo 搜索插件（quarksoo.cc）。

站点只有 GET /search.php?q=关键词 的 HTML 表格，行内是 pan.qoark.cn 短链。
短链会 301 到 pan.quark.cn/s/<分享ID> 或 drive.uc.cn/s/<分享ID>，必须换成
跳转后的真实地址，否则 AdapterRegistry 识别不出网盘类型，结果会被整条丢弃。

站点的搜索是按字符的松散匹配（例如 "三体" 会命中 "成何体统"、"群体"），
所以这里按关键词做标题过滤，只保留标题包含全部关键词的条目。
"""
from __future__ import annotations

import concurrent.futures
import html
import logging
import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from app.infrastructure.drive.adapter_registry import AdapterRegistry
from app.infrastructure.search.base import BaseSearchPlugin

logger = logging.getLogger(__name__)

SEARCH_URL = "https://quarksoo.cc/search.php"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": SEARCH_URL,
}
# 单次搜索最多解析多少条短链：每条 1-3 秒，并发解析，避免拖到插件超时。
MAX_RESOLVE = 12
ROW_RE = re.compile(
    r'<div class="drama-cell">(.*?)</div>\s*</td>\s*<td>\s*<a href="([^"]+)"[^>]*class="pan-link"',
    re.S,
)
TAG_RE = re.compile(r"<[^>]+>")
LEADING_NOISE_RE = re.compile(r"^[^0-9A-Za-z\u4e00-\u9fff]+")
SPACE_RE = re.compile(r"\s+")
REDIRECT_CODES = {301, 302, 303, 307, 308}


def _fetch_page(keyword: str) -> str:
    """抓取搜索结果页；站点偶尔卡住不返回，失败后重试一次。"""
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = requests.get(
                SEARCH_URL,
                params={"q": keyword},
                headers=HEADERS,
                timeout=(5, 8),
            )
            response.raise_for_status()
            response.encoding = "utf-8"
            return response.text
        except requests.RequestException as exc:
            last_error = exc
            if attempt == 0:
                time.sleep(1.0)
    raise RuntimeError(f"QuarkSoo 搜索请求失败：{last_error}")


def _clean_title(raw: str) -> str:
    text = html.unescape(TAG_RE.sub("", raw or ""))
    text = SPACE_RE.sub(" ", text).strip()
    # 去掉标题前的 🎞️ 之类的装饰符号
    return LEADING_NOISE_RE.sub("", text).strip()[:512]


def _matches(title: str, terms: list[str]) -> bool:
    folded = title.casefold()
    return all(term in folded for term in terms)


def _redirect_target(short_url: str, response: requests.Response) -> str | None:
    if response.status_code not in REDIRECT_CODES:
        return None
    location = urljoin(short_url, response.headers.get("location") or "").strip()
    if not location:
        return None
    parsed = urlparse(location)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    # 只保留系统认得的网盘域名，避免把无关跳转带进任务
    if not AdapterRegistry.detect_drive_type(location):
        return None
    return location


def _resolve(short_url: str) -> str | None:
    """跟随短链的 301，返回可被网盘适配器识别的真实分享地址。

    短链主机的响应时间实测在 0.3-5 秒之间且偶尔超时，HEAD 失败时用 GET
    （不读响应体）兜底一次，避免单个慢请求把有效结果丢掉。
    """
    for method in ("HEAD", "GET"):
        try:
            response = requests.request(
                method,
                short_url,
                headers=HEADERS,
                timeout=(3, 6),
                allow_redirects=False,
                stream=method == "GET",
            )
        except requests.RequestException:
            continue
        try:
            target = _redirect_target(short_url, response)
        finally:
            response.close()
        if target:
            return target
    return None


class QuarkSooPlugin(BaseSearchPlugin):
    key = "quarksoo"
    name = "QuarkSoo 短链"
    description = "抓取 quarksoo.cc 的分享短链，跟随 301 解析为夸克/UC 真实分享地址"
    timeout_seconds = 30.0

    def search(self, keyword: str) -> list[dict[str, Any]]:
        keyword = SPACE_RE.sub(" ", str(keyword or "")).strip()
        if not keyword:
            return []
        terms = [term for term in keyword.casefold().split(" ") if term]

        text = _fetch_page(keyword)

        rows = ROW_RE.findall(text)
        if not rows:
            if "drama-cell" in text:
                logger.warning("QuarkSoo 结果解析为空，页面结构可能已变化")
            return []

        candidates: list[tuple[str, str]] = []
        seen_short: set[str] = set()
        for raw_title, short_url in rows:
            title = _clean_title(raw_title)
            short_url = html.unescape(short_url).strip()
            if not title or not short_url.startswith("http") or short_url in seen_short:
                continue
            if not _matches(title, terms):
                continue
            seen_short.add(short_url)
            candidates.append((title, short_url))
            if len(candidates) >= MAX_RESOLVE:
                break

        if not candidates:
            return []

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(candidates)) as executor:
            resolved = list(executor.map(lambda item: _resolve(item[1]), candidates))

        failed = sum(1 for url in resolved if not url)
        if failed:
            logger.warning("QuarkSoo 短链解析失败 %d/%d 条", failed, len(candidates))

        results: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        for (title, short_url), url in zip(candidates, resolved):
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            results.append(
                {
                    "taskname": title,
                    "shareurl": url,
                    "datetime": None,
                    "content": f"来源：QuarkSoo；原始短链：{short_url}",
                }
            )
        return results
