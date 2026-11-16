"""Account operations used by the interactive authentication workflow."""
from typing import Any
from app.application import drive_accounts
from app.domain.ports.drive_account import DriveAccountRecord
from app.domain.ports.drive_account_auth import Config

class AccountAuthenticationGateway:
    def get(self, db: Any, account_id: int) -> DriveAccountRecord:
        return drive_accounts.get_drive_account(db, account_id)

    def prepare(self, db: Any, account_id: int, expected_version: int | None = None) -> int:
        return drive_accounts.prepare_drive_account_auth(db, account_id, expected_version)

    def probe(self, db: Any, account_id: int, *, config: Config | None = None,
              auth_completed: bool = False, expected_version: int | None = None) -> DriveAccountRecord:
        return drive_accounts.probe_drive_account(db, account_id, config=config,
            auth_completed=auth_completed, expected_version=expected_version)
