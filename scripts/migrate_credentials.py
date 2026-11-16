"""Offline, explicit-file credential conversion. Never runs at application startup."""
import argparse
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.core.credential_crypto import load_keyring
from app.infrastructure.persistence.credential_migration import migrate_credentials


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--backup', type=Path, required=True)
    parser.add_argument('--rotate', action='store_true')
    args = parser.parse_args()
    source, backup = args.database.resolve(), args.backup.resolve()
    if not source.is_file() or source == backup or backup.exists():
        parser.error('Existing source and distinct new backup path required')
    load_keyring(required=True)
    with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src:
        with sqlite3.connect(backup) as dest:
            src.backup(dest)
            if dest.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Backup validation failed')
    engine = create_engine('sqlite:///' + source.as_posix())
    try:
        with Session(engine) as db, db.begin():
            changed = migrate_credentials(db, rotate=args.rotate)
        print(f'Credential conversion completed: {changed} accounts; backup retained')
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
