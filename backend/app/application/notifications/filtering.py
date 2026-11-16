"""Pure notification keyword policy."""
from typing import Any

def _should_send(config: dict[str, Any], title: str, content: str) -> bool:
    """检查通知是否应该发送（基于关键词过滤/排除）"""
    text = f"{title} {content}".lower()

    # 排除关键词
    exclude_keywords = [str(k).strip().lower() for k in (config.get("__exclude_keywords") or []) if str(k).strip()]
    if exclude_keywords:
        exclude_mode = str(config.get("__exclude_mode") or "any").strip()
        if exclude_mode == "all":
            if exclude_keywords and all(k in text for k in exclude_keywords):
                return False
        else:
            if any(k in text for k in exclude_keywords):
                return False

    # 包含关键词（只有包含才发送）
    filter_keywords = [str(k).strip().lower() for k in (config.get("__filter_keywords") or []) if str(k).strip()]
    if filter_keywords:
        filter_mode = str(config.get("__filter_mode") or "all").strip()
        if filter_mode == "any":
            if not any(k in text for k in filter_keywords):
                return False
        else:
            if not all(k in text for k in filter_keywords):
                return False

    return True

