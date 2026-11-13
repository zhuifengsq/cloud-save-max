"""On-demand TMDB artwork metadata; image bytes remain on TMDB's CDN."""

import re
from typing import Literal

from app.application.container import tmdb_client_cls
from app.application.tmdb_settings import get_or_create_tmdb_setting, get_tmdb_runtime_config
from app.core.runtime.tx import end_read_tx

TMDBClient = tmdb_client_cls()
_IMAGE_PATH = re.compile(r"/[a-zA-Z0-9_-]+\.(?:jpg|jpeg|png|webp)", re.IGNORECASE)


class TMDBNotConfigured(Exception):
    pass


class TMDBImagesUnavailable(Exception):
    pass


def _image_metadata(items: list) -> list[dict]:
    result = []
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        path = item.get("file_path")
        width, height = item.get("width"), item.get("height")
        if (
            not isinstance(path, str) or not _IMAGE_PATH.fullmatch(path)
            or path in seen
            or type(width) is not int or type(height) is not int
            or width <= 0 or height <= 0
        ):
            continue
        language = item.get("iso_639_1")
        result.append({
            "file_path": path, "width": width, "height": height,
            "language": language if isinstance(language, str) else None,
        })
        seen.add(path)
    return result


def get_tmdb_images(db, *, media_type: Literal["movie", "tv"], tmdb_id: int) -> dict:
    # This is a read-only request entry point: freeze configuration and release
    # the database transaction before contacting the external service.
    cfg = get_tmdb_runtime_config(get_or_create_tmdb_setting(db))
    api_key = str(cfg.get("api_key") or "").strip()
    language = str(cfg.get("language") or "zh-CN")
    end_read_tx(db)
    if not api_key:
        raise TMDBNotConfigured("请先配置 TMDB API Key，再选择更多配图")
    client = TMDBClient(api_key, language=language)
    try:
        raw = client.get_images(media_type, tmdb_id)
    finally:
        client.session.close()
    if not isinstance(raw, dict) or any(not isinstance(raw.get(key), list) for key in ("posters", "backdrops")):
        raise TMDBImagesUnavailable("配图获取失败，请稍后重试")
    return {key: _image_metadata(raw[key]) for key in ("posters", "backdrops")}
