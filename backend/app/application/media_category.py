"""媒体分类（追剧任务列表的「分类」筛选）。

口径（命中即止，从上到下）：
1. 动漫：genres 含 16（Animation），或类型名含 animation/动画
2. 综艺：genres 含 10764 / 10767 / 10763（真人秀 / 脱口秀 / 新闻）
3. 电影：media_type == movie
4. 国剧：origin_country 含 CN
5. 韩剧：KR
6. 日剧：JP
7. 美剧：US
8. 港台剧：HK / TW
9. 其他剧：其余 tv
10. 未分类：没绑 TMDB，或缓存里没有 payload

两个容易踩错的点：
- 国剧/韩剧必须看 origin_country 而不是 original_language：港剧的 original_language
  是 cn，合拍片更不可靠（所以 zh 不做语言兜底）；
- 动漫/综艺的判断优先于国家维度，否则「日本动漫」会被算成日剧。

为什么放在 application 层且是纯函数：任务列表已经通过
`app/application/tmdb_view.load_tmdb_payload_map()` 批量取到了 payload
（tmdb_media_cache.payload_json，即 TMDB 原始详情），这里只是顺手算一个分类字段，
**零新增查询、零网络请求**——列表页不能因为 TMDB 不可达而变慢或报错。
"""
from __future__ import annotations

from typing import Any

CATEGORY_ANIME = "动漫"
CATEGORY_VARIETY = "综艺"
CATEGORY_MOVIE = "电影"
CATEGORY_CN = "国剧"
CATEGORY_KR = "韩剧"
CATEGORY_JP = "日剧"
CATEGORY_US = "美剧"
CATEGORY_HK_TW = "港台剧"
CATEGORY_OTHER_TV = "其他剧"
CATEGORY_UNKNOWN = "未分类"

# 前端筛选条的展示顺序（只显示列表里实际出现过的分类）。
CATEGORY_ORDER: tuple[str, ...] = (
    CATEGORY_CN,
    CATEGORY_KR,
    CATEGORY_JP,
    CATEGORY_US,
    CATEGORY_HK_TW,
    CATEGORY_VARIETY,
    CATEGORY_ANIME,
    CATEGORY_MOVIE,
    CATEGORY_OTHER_TV,
    CATEGORY_UNKNOWN,
)

ANIME_GENRE_IDS: frozenset[int] = frozenset({16})
VARIETY_GENRE_IDS: frozenset[int] = frozenset({10764, 10767, 10763})

_ANIME_GENRE_NAME_HINTS: tuple[str, ...] = ("animation", "动画")
_VARIETY_GENRE_NAME_HINTS: tuple[str, ...] = ("reality", "talk", "news", "真人秀", "脱口秀", "新闻")

_COUNTRY_ORDER: tuple[tuple[str, frozenset[str]], ...] = (
    (CATEGORY_CN, frozenset({"CN"})),
    (CATEGORY_KR, frozenset({"KR"})),
    (CATEGORY_JP, frozenset({"JP"})),
    (CATEGORY_US, frozenset({"US"})),
    (CATEGORY_HK_TW, frozenset({"HK", "TW"})),
)

# 只在 origin_country / production_countries 都缺时才用；zh 故意不兜底（cn/tw/hk 无法区分）。
_LANGUAGE_TO_COUNTRY: dict[str, str] = {"ko": "KR", "ja": "JP", "en": "US"}


def _genre_sets(payload: dict[str, Any]) -> tuple[set[int], set[str]]:
    ids: set[int] = set()
    names: set[str] = set()
    genres = payload.get("genres")
    if isinstance(genres, list):
        for item in genres:
            if not isinstance(item, dict):
                continue
            try:
                genre_id = int(item.get("id"))
            except (TypeError, ValueError):
                genre_id = 0
            if genre_id > 0:
                ids.add(genre_id)
            name = str(item.get("name") or "").strip().lower()
            if name:
                names.add(name)
    return ids, names


def _country_codes(payload: dict[str, Any]) -> set[str]:
    codes: set[str] = set()
    origin = payload.get("origin_country")
    if isinstance(origin, list):
        for code in origin:
            text = str(code or "").strip().upper()
            if text:
                codes.add(text)
    if codes:
        return codes
    produced = payload.get("production_countries")
    if isinstance(produced, list):
        for item in produced:
            if not isinstance(item, dict):
                continue
            text = str(item.get("iso_3166_1") or "").strip().upper()
            if text:
                codes.add(text)
    if codes:
        return codes
    language = str(payload.get("original_language") or "").strip().lower()
    fallback = _LANGUAGE_TO_COUNTRY.get(language)
    return {fallback} if fallback else set()


def _matches_name_hint(names: set[str], hints: tuple[str, ...]) -> bool:
    return any(hint in name for name in names for hint in hints)


def categorize(media_type: str | None, payload: Any) -> str:
    """把 (media_type, TMDB 详情 payload) 映射成分类标签。

    纯函数、绝不抛异常：payload 缺失/解析失败/字段形态异常一律「未分类」，
    保证任务不会因为分类算不出来而从列表里消失。
    """
    if not isinstance(payload, dict) or not payload:
        return CATEGORY_UNKNOWN

    ids, names = _genre_sets(payload)
    if ids & ANIME_GENRE_IDS or _matches_name_hint(names, _ANIME_GENRE_NAME_HINTS):
        return CATEGORY_ANIME
    if ids & VARIETY_GENRE_IDS or _matches_name_hint(names, _VARIETY_GENRE_NAME_HINTS):
        return CATEGORY_VARIETY

    normalized_type = str(media_type or "").strip().lower()
    if normalized_type == "movie":
        return CATEGORY_MOVIE

    codes = _country_codes(payload)
    for category, wanted in _COUNTRY_ORDER:
        if codes & wanted:
            return category

    if normalized_type == "tv":
        return CATEGORY_OTHER_TV
    return CATEGORY_UNKNOWN
