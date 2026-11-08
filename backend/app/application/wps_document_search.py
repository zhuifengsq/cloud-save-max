"""Anonymous, read-only WPS document search; shared across dynamically loaded plugins."""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
import uuid
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

import requests
from cachetools import LRUCache

DEFAULT_URL = 'https://www.kdocs.cn/l/caplPyLsLBDB'
DRIVES = {'pan.quark.cn': 'quark', 'pan.baidu.com': 'baidu', 'pan.xunlei.com': 'xunlei'}
CACHE_TTL = 15 * 60
_cache = LRUCache(maxsize=16)
_lock = threading.RLock()
_fetch_lock = threading.Lock()
_generation = 0
_hot_cache = LRUCache(maxsize=16)
_hot_cache_lock = threading.Lock()


def validate_config(config: dict) -> dict:
    url = str(config.get('document_url', DEFAULT_URL)).strip()
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.netloc not in {'www.kdocs.cn', 'kdocs.cn'}
            or not re.fullmatch(r'/l/[A-Za-z0-9]+', parsed.path)):
        raise ValueError('请填写 https://www.kdocs.cn/l/ 开头的 WPS 文档分享链接')
    drives = config.get('drive_types', list(DRIVES.values()))
    if not isinstance(drives, list) or not drives or any(d not in DRIVES.values() for d in drives):
        raise ValueError('请至少选择一种支持的网盘：夸克、百度、迅雷')
    return {'document_url': 'https://www.kdocs.cn' + parsed.path, 'drive_types': list(dict.fromkeys(drives))}


def _read_response(response: requests.Response) -> str:
    if response.is_redirect or response.status_code in (401, 403):
        raise ValueError('无法匿名读取该文档，请确认链接公开可查看且无需密码')
    response.raise_for_status()
    chunks = []
    size = 0
    for chunk in response.iter_content(65536):
        size += len(chunk)
        if size > 16 * 1024 * 1024:
            raise ValueError('WPS 文档超过 16 MB，暂不支持读取')
        chunks.append(chunk)
    return b''.join(chunks).decode('utf-8')


def fetch_document(url: str) -> dict:
    file_id = urlsplit(url).path.rsplit('/', 1)[-1]
    with requests.Session() as session:
        # 与公开页面的访客返回流程一致；不是用户登录 Cookie。
        session.cookies.set('hadSingleSign', 'true', domain='.kdocs.cn')
        with session.get(url, timeout=(4, 8), stream=True, allow_redirects=False) as response:
            html = _read_response(response)
        token = re.search(r'"csrf_token"\s*:\s*("(?:\\.|[^"\\])*")', html)
        if not token:
            raise ValueError('无法匿名读取该文档，请确认链接公开可查看且无需密码')
        body = {'connid': uuid.uuid4().hex, 'args': {
            'password': '', 'readonly': True, 'modifyPassword': '', 'sync': True,
            'startVersion': 0, 'endVersion': 0, 'autoSlim': True, 'connRenderMode': 0,
        }}
        with session.post(
            f'https://www.kdocs.cn/api/v3/office/file/{file_id}/open/otl', json=body,
            headers={'x-csrf-rand': json.loads(token.group(1)), 'Origin': 'https://www.kdocs.cn', 'Referer': url},
            timeout=(4, 8), stream=True, allow_redirects=False,
        ) as response:
            data = json.loads(_read_response(response))
    content = data.get('content') if isinstance(data, dict) else None
    if not isinstance(content, dict) or content.get('type') != 'doc':
        raise ValueError('WPS 未返回可解析正文，文档可能受限或格式不支持')
    return content


def _document_blocks(node: dict):
    if not isinstance(node, dict):
        return
    if node.get('type') == 'horizontal_rule':
        yield None
    elif node.get('type') in {'paragraph', 'heading'}:
        texts, urls = [], []

        def inline(part):
            if part.get('type') == 'text':
                texts.append(part.get('text', ''))
            for mark in part.get('marks', []):
                href = mark.get('attrs', {}).get('href')
                if isinstance(href, str):
                    urls.append(href)
            for child in part.get('content', []):
                inline(child)

        inline(node)
        text = unicodedata.normalize('NFKC', ''.join(texts)).strip()
        urls.extend(re.findall(r'https?://[^\s<>"\u3000]+', text))
        yield text, list(dict.fromkeys(urls))
    else:
        for child in node.get('content', []):
            yield from _document_blocks(child)


def _share_link(raw_url: str):
    url = raw_url.rstrip('。，,;；)）')
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    drive = DRIVES.get(parsed.netloc)
    if parsed.scheme in {'http', 'https'} and drive and parsed.path.startswith('/s/'):
        return {'drive': drive, 'url': url}
    return None


def _with_passcode(url: str, passcode: str) -> str:
    parsed = urlsplit(url)
    if parse_qs(parsed.query).get('pwd'):
        return url
    query = parsed.query + ('&' if parsed.query else '') + urlencode({'pwd': passcode})
    return urlunsplit(parsed._replace(query=query))


PASSCODE = re.compile(r'提取码\s*[:：]?\s*([A-Za-z0-9]{4,8})')
HOT_HEADING = re.compile(
    r'^[^\w]*?(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*日?\s*\(?热门更新\)?$'
)


def parse_hot_updates(content: dict) -> dict:
    blocks = list(_document_blocks(content))
    headings = {}
    for index, block in enumerate(blocks):
        match = HOT_HEADING.fullmatch(block[0]) if block is not None else None
        if match:
            try:
                headings[index] = datetime(*map(int, match.groups()))
            except ValueError:
                continue

    missing = '未找到完整的热门更新区块'
    if not headings:
        raise ValueError(missing)
    heading = max(headings, key=headings.__getitem__)
    start = heading + 1
    while start < len(blocks) and blocks[start] == ('', []):
        start += 1
    if start == len(blocks) or blocks[start] is not None:
        raise ValueError(missing)
    end = next((i for i in range(start + 1, len(blocks)) if blocks[i] is None), None)
    if end is None or any(start < i < end for i in headings):
        raise ValueError(missing)

    entries = []
    for block in blocks[start + 1:end]:
        text, urls = block
        passcode = PASSCODE.search(text)
        if urls:
            links = [link for url in urls if (link := _share_link(url))]
            if passcode and len(links) == 1 and links[0]['drive'] in {'baidu', 'xunlei'}:
                links[0]['url'] = _with_passcode(links[0]['url'], passcode[1])
            if entries:
                existing = entries[-1]['links']
                existing.extend(link for link in links if link not in existing)
        elif passcode:
            if entries and entries[-1]['links']:
                previous = entries[-1]['links'][-1]
                if previous['drive'] in {'baidu', 'xunlei'}:
                    previous['url'] = _with_passcode(previous['url'], passcode[1])
        elif text and re.search(r'[^\W\d_]', text):
            entries.append({'title': text, 'links': []})

    return {'date': headings[heading].date().isoformat(), 'entries': entries}


def read_hot_updates(url: str) -> dict:
    url = validate_config({'document_url': url})['document_url']
    with _hot_cache_lock:
        cached = _hot_cache.get(url)
        if cached is not None and time.monotonic() - cached[0] < CACHE_TTL:
            return cached[1]
        result = parse_hot_updates(fetch_document(url))
        _hot_cache[url] = (time.monotonic(), result)
        return result


def clear_hot_updates_cache(url: str) -> None:
    url = validate_config({'document_url': url})['document_url']
    with _hot_cache_lock:
        _hot_cache.pop(url, None)


def parse_document(content: dict) -> list[dict]:
    rows, seen = [], set()
    title, date, previous = '', '', None
    for block in _document_blocks(content):
        if block is None:
            continue
        text, urls = block
        date_match = re.fullmatch(r'(20\d{2})[.年/-](\d{1,2})[.月/-](\d{1,2})日?.*', text)
        if date_match:
            try:
                date = datetime(*map(int, date_match.groups())).strftime('%Y-%m-%d')
            except ValueError:
                date = ''
            title, previous = '', None
            continue
        passcode = PASSCODE.search(text)
        if passcode and not urls:
            if previous and previous['drive_type'] in {'baidu', 'xunlei'}:
                previous['shareurl'] = _with_passcode(previous['shareurl'], passcode[1])
            continue
        if urls:
            for raw_url in urls:
                link = _share_link(raw_url)
                if not title or not link or link['url'] in seen:
                    continue
                url, drive = link['url'], link['drive']
                seen.add(url)
                previous = {'taskname': title, 'shareurl': url, 'datetime': date or None,
                            'drive_type': drive, 'content': f'WPS 文档标注：{title}'}
                rows.append(previous)
            continue
        if not text:
            continue
        # 明确结束资源段落，防止说明区的链接被归到上一部剧。
        if re.search(r'不用打开|使用方法|使用说明|搜索方法|注意[:：]|以下是|分割线|进群|和谐|点赞|留言', text):
            title, previous = '', None
            continue
        if len(text) <= 250 and not re.fullmatch(r'[\W\d]+', text):
            title, previous = text, None
    return rows


def clear_cache(url: str) -> None:
    global _generation
    with _lock:
        _generation += 1
        _cache.pop(url, None)


def cache_status(url: str) -> dict:
    with _lock:
        entry = _cache.get(url)
        return {'cached': bool(entry), 'count': len(entry['rows']) if entry else 0,
                'updated_at': entry['updated_at'] if entry else None,
                'expired': bool(entry and time.monotonic() - entry['at'] >= CACHE_TTL)}


def _cached_rows(url: str) -> list[dict]:
    # 串行获取，多个搜索不会同时下载同一份大文档。
    with _fetch_lock:
        with _lock:
            entry = _cache.get(url)
            if entry and time.monotonic() - entry['at'] < CACHE_TTL:
                return entry['rows']
            generation = _generation
        rows = parse_document(fetch_document(url))
        if not rows:
            raise ValueError('文档中未识别到带标题的网盘链接，请检查文档格式')
        with _lock:
            if generation == _generation:
                _cache[url] = {'rows': rows, 'at': time.monotonic(),
                               'updated_at': datetime.now().astimezone().isoformat(timespec='seconds')}
        return rows


def search(keyword: str, config: dict) -> list[dict]:
    words = unicodedata.normalize('NFKC', keyword).casefold().split()
    if not words:
        return []
    rows = _cached_rows(config['document_url'])
    return [dict(row) for row in rows if row['drive_type'] in config['drive_types']
            and all(word in row['taskname'].casefold() for word in words)]
