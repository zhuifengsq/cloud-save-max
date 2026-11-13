from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any


from app.core.settings import settings

logger = logging.getLogger(__name__)


def preview_token_ttl_seconds() -> int:
    try:
        ttl = int(settings.drama_lead_in_preview_token_ttl_seconds)
    except Exception:
        ttl = 600
    return max(30, min(ttl, 3600))


def _token_secret() -> bytes:
    return str(settings.jwt_secret_key or settings.app_name or "cloud-save-max").encode(
        "utf-8"
    )


def create_preview_token(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = hmac.new(
        _token_secret(), body.encode("ascii"), hashlib.sha256
    ).hexdigest()
    return f"v1.{body}.{signature}"


def parse_preview_token(
    token: str, *, now: float | None = None
) -> dict[str, Any] | None:
    if not isinstance(token, str):
        return None
    parts = token.strip().split(".")
    if len(parts) != 3 or parts[0] != "v1" or not parts[1] or not parts[2]:
        return None
    body = parts[1]
    expected = hmac.new(
        _token_secret(), body.encode("ascii"), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, parts[2]):
        return None
    padded = body + "=" * (-len(body) % 4)
    try:
        payload = json.loads(
            base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
        )
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    try:
        expires_at = int(payload.get("exp") or 0)
    except (TypeError, ValueError):
        return None
    current = time.time() if now is None else float(now)
    if expires_at <= current:
        return None
    return payload
