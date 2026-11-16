"""Redact credential fields and URL query strings before log output."""
import re
from urllib.parse import urlsplit, urlunsplit

_URL = re.compile(r'https?://[^\s\'"<>]+', re.IGNORECASE)
_FIELDS = re.compile(
    r'''(?ix)(["']?(?:authorization|proxy-authorization|cookie|set-cookie|password|passwd|secret|
    [a-z_]*token|__puus|__pus|bduss|stoken|kps|sign|vcode|ssoncookie|loginuuid)["']?\s*[:=]\s*)
    (?:'[^'\r\n]*'|"[^"\r\n]*"|[^\s,;}]+)''')
_HEADERS = re.compile(r'(?im)(\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*)[^\r\n]+')


def _safe_url(match: re.Match[str]) -> str:
    try:
        url = urlsplit(match.group())
        authority = url.netloc.rsplit('@', 1)[-1]
        return urlunsplit((url.scheme, authority, url.path, '[REDACTED]' if url.query else '', ''))
    except ValueError:
        return '[REDACTED URL]'


def redact_log_text(value: str) -> str:
    value = _URL.sub(_safe_url, value)
    value = _HEADERS.sub(r'\1[REDACTED]', value)
    return _FIELDS.sub(r'\1[REDACTED]', value)
