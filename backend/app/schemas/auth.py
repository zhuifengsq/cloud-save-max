from pydantic import BaseModel
from typing import Literal


class LoginThrottleError(BaseModel):
    code: Literal['AUTH_LOGIN_THROTTLED'] = 'AUTH_LOGIN_THROTTLED'
    message: str
    detail: str | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class MeOut(BaseModel):
    id: int
    username: str
    email: str
    roles: list[str]
    permissions: list[str]


class LoginOut(TokenOut):
    user: MeOut
