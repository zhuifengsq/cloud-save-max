"""Explicit offline migration; caller owns backup, transaction and lifecycle."""
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.credential_crypto import load_keyring
from app.core.account_privacy import SAFE_ACCOUNT_ERROR, sanitize_profile_json
from app.infrastructure.persistence.models.drive_account import DriveAccount


def migrate_credentials(db: Session, *, rotate: bool = False) -> int:
    load_keyring(required=True)
    changed = 0
    for account in db.scalars(select(DriveAccount)):
        profile = sanitize_profile_json(account.profile_json)
        error = SAFE_ACCOUNT_ERROR if account.last_error else None
        metadata_changed = profile != account.profile_json or error != account.last_error
        account.profile_json, account.last_error = profile, error
        cookie, config = account.cookie, account.config_json
        if account.credential_version == 1 and not rotate:
            if metadata_changed:
                db.flush()
                changed += 1
            continue
        account.cookie, account.config_json = cookie, config
        db.flush()
        changed += 1
    return changed
