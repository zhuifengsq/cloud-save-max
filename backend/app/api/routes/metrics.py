from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Query, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import forbidden, unauthorized
from app.core.metrics import metrics_store
from app.core.permissions import AUDIT_READ
from app.core.settings import settings
from app.application.container import get_db


router = APIRouter()

# auto_error=False：没带 Authorization 时不要直接 401，交给下面的依赖按配置决定
# （配了 METRICS_TOKEN 的部署是给监控系统抓取用的，不带登录态）。
_optional_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def require_metrics_access(
    request: Request,
    token: str | None = Depends(_optional_oauth2),
    query_token: str | None = Query(default=None, alias="token"),
    db: Session = Depends(get_db),
) -> None:
    """指标接口鉴权。

    - 配置了 METRICS_TOKEN：只认 token（Authorization: Bearer 或 ?token=），供 Prometheus 之类的抓取器使用；
    - 未配置：要求登录用户且具备 audit:read（运维只读权限）。
    """
    expected = (settings.metrics_token or "").strip()
    if expected:
        supplied = [value for value in (token, query_token) if value]
        if any(hmac.compare_digest(str(value), expected) for value in supplied):
            return
        raise unauthorized("METRICS_UNAUTHORIZED", "指标接口未授权")

    if not token:
        raise unauthorized("AUTH_INVALID_TOKEN", "登录已失效")
    current = get_current_user(request=request, token=token, db=db)
    if AUDIT_READ not in current.permissions:
        raise forbidden("AUTH_FORBIDDEN", "权限不足", detail=AUDIT_READ)


@router.get("/metrics")
def metrics(_: None = Depends(require_metrics_access)):
    return {"metrics": metrics_store.snapshot(), "counters": metrics_store.counters()}
