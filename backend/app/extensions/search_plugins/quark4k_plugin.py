"""Search public Quark4K discussions for supported cloud-drive share links.

Adapted from pan-relay's Quark4K plugin (Copyright 2025 ucmao, MIT license).
See PAN-RELAY-LICENSE in this directory.
"""

from __future__ import annotations

import concurrent.futures
import html
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import requests

from app.extensions.runtime.adapter_registry import AdapterRegistry
from app.extensions.search_plugins.base import BaseSearchPlugin


LINK_RE = re.compile(r"https?://[^\s<>'\"，。；、]+", re.IGNORECASE)


def _clean_text(value: Any) -> str:
    return " ".join(html.unescape(str(value or "")).split()).strip()


class _PostParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_data(self, data: str) -> None:
        self.text_parts.append(data)


class Quark4KPlugin(BaseSearchPlugin):
    key = "quark4k"
    name = "Quark4K"
    description = "搜索 Quark4K 论坛公开讨论中的网盘分享链接"
    timeout_seconds = 12.0
    endpoint = "https://quark4k.com/api/discussions"
    page_size = 50

    def _fetch_page(self, keyword: str, offset: int) -> dict[str, Any]:
        response = requests.get(
            self.endpoint,
            params={
                "include": "user,lastPostedUser,mostRelevantPost,mostRelevantPost.user,tags,tags.parent,firstPost",
                "filter[q]": keyword,
                "sort": "",
                "page[offset]": offset,
                "page[limit]": self.page_size,
            },
            headers={
                "Accept": "application/json, text/plain, */*",
                "Referer": "https://quark4k.com/",
                "User-Agent": "Mozilla/5.0 (compatible; CloudSaveMax/1.0)",
            },
            timeout=(3, 6),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Quark4K 返回的 JSON 不是对象")
        return payload

    def search(self, keyword: str) -> list[dict[str, Any]]:
        keyword = _clean_text(keyword)
        if not keyword:
            return []

        payloads: list[dict[str, Any]] = []
        errors: list[Exception] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(self._fetch_page, keyword, offset) for offset in (0, self.page_size)]
            for future in concurrent.futures.as_completed(futures):
                try:
                    payloads.append(future.result())
                except Exception as exc:
                    errors.append(exc)
        if not payloads and errors:
            raise RuntimeError(f"Quark4K 页面请求失败：{errors[0]}") from errors[0]

        terms = keyword.casefold().split()
        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for payload in payloads:
            posts = {
                str(item.get("id")): item
                for item in payload.get("included") or []
                if isinstance(item, dict) and item.get("type") == "posts"
            }
            for discussion in payload.get("data") or []:
                if not isinstance(discussion, dict):
                    continue
                attributes = discussion.get("attributes") or {}
                title = _clean_text(attributes.get("title"))
                if not title or any(term not in title.casefold() for term in terms):
                    continue
                relationships = discussion.get("relationships") or {}
                post_ref = (relationships.get("mostRelevantPost") or {}).get("data") or (relationships.get("firstPost") or {}).get("data") or {}
                post = posts.get(str(post_ref.get("id"))) or {}
                content_html = str((post.get("attributes") or {}).get("contentHtml") or "")
                parser = _PostParser()
                parser.feed(content_html)
                text = " ".join(parser.text_parts)
                links = [*parser.links, *(match.group(0) for match in LINK_RE.finditer(text))]
                for value in links:
                    url = html.unescape(str(value or "")).strip().rstrip(".,;，。；、)")
                    parsed = urlparse(url)
                    if parsed.scheme not in {"http", "https"} or not AdapterRegistry.detect_drive_type(url) or url in seen:
                        continue
                    seen.add(url)
                    results.append({
                        "taskname": title,
                        "shareurl": url,
                        "datetime": str(attributes.get("createdAt") or "") or None,
                        "content": _clean_text(text) or None,
                    })
                    if len(results) >= 50:
                        return results
        return results
