"""Allowlisted account metadata shared by persistence and API serialization."""
import json

PUBLIC_PROFILE_FIELDS = frozenset({
    'drive_type', 'drive_name', 'nickname', 'username', 'used_space', 'total_space',
    'member_type', 'member_status', 'avatar', 'avatar_url',
})
SAFE_ACCOUNT_ERROR = '账号操作失败，请检查登录状态或重新认证'


def public_account_profile(profile: object) -> dict[str, str | int | float | bool | None]:
    if not isinstance(profile, dict):
        return {}
    return {key: value for key, value in profile.items() if key in PUBLIC_PROFILE_FIELDS
            and (value is None or isinstance(value, (str, int, float, bool)))}


def sanitize_profile_json(raw: str | None) -> str | None:
    if raw is None:
        return None
    try:
        return json.dumps(public_account_profile(json.loads(raw)), ensure_ascii=False)
    except (TypeError, ValueError):
        return None
