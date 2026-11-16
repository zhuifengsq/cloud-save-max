"""Own the atomic first-administrator transaction; no external IO is performed."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request, Response
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session

from app.application import audit, auth, setup
from app.application.container import system_settings_repo, transaction, users_repo
from app.core.errors import ApiError


@dataclass(frozen=True)
class InitializedAdmin:
    id: int
    username: str
    email: str
    roles: list[str]
    permissions: list[str]
    access_token: str


def _already_initialized() -> ApiError:
    return ApiError(code="SETUP_ALREADY_INITIALIZED", message="系统已初始化", http_status=409)


def initialize_admin(
    db: Session, *, username: str, email: str, password: str,
    request: Request, response: Response,
) -> InitializedAdmin:
    """Claim, user, roles, refresh token and audit either commit together or all roll back."""
    auth.ensure_password_policy(password)
    try:
        with transaction(db):
            if setup.is_initialized(db):
                raise _already_initialized()
            # End the read snapshot before competing for SQLite's writer slot.
            # This use case owns a fresh request session with no earlier writes.
            db.rollback()
            if not system_settings_repo().claim_initial_admin(db):
                raise _already_initialized()
            if users_repo().has_any_user(db):
                raise _already_initialized()
            user = setup.create_initial_admin(db, username=username, email=email, password=password)
            db.flush()
            # Read without filling the process permission cache before commit.
            roles, permission_set = users_repo().load_roles_permissions(db, user.id)
            permissions = sorted(permission_set)
            access_token = auth.issue_tokens(
                db, user=user, permissions=permissions, request=request, response=response,
            )
            audit.write_audit_log(
                db, actor_user_id=None, action="setup.init_admin", target_type="user",
                target_id=str(user.id), ip=request.client.host if request.client else None,
                user_agent=request.headers.get("user-agent"), success=True,
            )
            result = InitializedAdmin(
                access_token=access_token, id=user.id, username=user.username, email=user.email,
                roles=roles, permissions=permissions,
            )
        return result
    except (OperationalError, ProgrammingError) as exc:
        raise ApiError(code="DB_NOT_READY", message="数据库初始化中，请稍后重试", http_status=503) from exc
