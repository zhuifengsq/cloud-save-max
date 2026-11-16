"""Fixed-clock characterization for the time-policy design; no host TZ mutation."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import Request, Response
from jose import jwt
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.application import auth
from app.core import security
from app.core.errors import ApiError
from app.infrastructure.persistence.models.refresh_token import RefreshToken


@pytest.mark.parametrize('offset', [-5, 0, 8])
def test_access_token_uses_unix_instant_and_configured_lifetime(monkeypatch, offset):
    instant = datetime(2030, 1, 1, 12, 30, tzinfo=timezone.utc)
    zone = timezone(timedelta(hours=offset))
    local = instant.astimezone(zone).replace(tzinfo=None)

    class FixedLocalDateTime(datetime):
        @classmethod
        def now(cls):
            return cls(local.year, local.month, local.day, local.hour, local.minute)

        def timestamp(self):
            return datetime(self.year, self.month, self.day, self.hour, self.minute,
                            self.second, self.microsecond, tzinfo=zone).timestamp()

    monkeypatch.setattr(security, 'datetime', FixedLocalDateTime)
    monkeypatch.setattr(security.settings, 'access_token_expires_seconds', 7200)
    token = security.create_access_token('fake-user', [])
    claims = jwt.get_unverified_claims(token)
    assert claims['iat'] == int(instant.timestamp())
    assert claims['exp'] - claims['iat'] == 7200


@pytest.mark.parametrize('offset', [-5, 0, 8])
@pytest.mark.parametrize('seconds_before_expiry', [-1, 0, 1])
def test_aware_refresh_expiry_boundary_in_each_simulated_local_timezone(
    monkeypatch, offset, seconds_before_expiry,
):
    expiry = datetime(2030, 1, 1, 12, 30, tzinfo=timezone.utc)
    zone = timezone(timedelta(hours=offset))

    class AwareExpiry(datetime):
        def astimezone(self, tz=None):
            return expiry.astimezone(tz or zone)

    stored = SimpleNamespace(
        expires_at=AwareExpiry(2030, 1, 1, 12, 30, tzinfo=timezone.utc),
        revoked_at=None, user_id=1,
    )
    now = (expiry - timedelta(seconds=seconds_before_expiry)).astimezone(zone).replace(tzinfo=None)
    monkeypatch.setattr(auth, '_utcnow', lambda: now)
    monkeypatch.setattr(auth, 'refresh_tokens_repo', lambda: SimpleNamespace(get_by_token_hash=lambda *_: stored))
    monkeypatch.setattr(auth, 'users_repo', lambda: SimpleNamespace(get_user=lambda *_: SimpleNamespace(id=1, is_active=True)))
    monkeypatch.setattr(auth, 'load_user_roles_permissions', lambda *_: ([], []))
    monkeypatch.setattr(auth, 'issue_tokens', lambda *args, **kwargs: 'fake-access')
    request = Request({'type': 'http', 'headers': [(b'cookie', f'{auth.settings.refresh_token_cookie_name}=fake-refresh'.encode())]})
    if seconds_before_expiry <= 0:
        with pytest.raises(ApiError) as error:
            auth.refresh_access_token(None, request=request, response=Response())
        assert error.value.code == 'AUTH_REFRESH_EXPIRED'
    else:
        user, access = auth.refresh_access_token(None, request=request, response=Response())
        assert user.id == 1 and access == 'fake-access'
        assert stored.revoked_at == now


def test_sqlite_refresh_expiry_loses_aware_offset_and_cannot_identify_origin(tmp_path):
    engine = create_engine('sqlite:///' + (tmp_path / 'time.db').as_posix())
    RefreshToken.__table__.create(engine)
    with Session(engine) as db:
        db.add(RefreshToken(user_id=1, token_hash='fake-hash', expires_at=datetime(2030, 1, 1, 20, 30, tzinfo=timezone(timedelta(hours=8)))))
        db.commit()
    with Session(engine) as db:
        expiry = db.scalar(select(RefreshToken.expires_at))
        assert expiry == datetime(2030, 1, 1, 20, 30)
        assert expiry.tzinfo is None
        # Existing normalization leaves naive values unchanged. A host moved
        # from UTC+8 to UTC cannot infer their issuer's zone from these bytes.
        assert auth._normalize_dt(expiry) == expiry
        utc_wall_at_actual_expiry = datetime(2030, 1, 1, 12, 30)
        assert auth._normalize_dt(expiry) > utc_wall_at_actual_expiry
    engine.dispose()
