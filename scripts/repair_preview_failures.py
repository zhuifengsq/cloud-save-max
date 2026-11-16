"""Offline repair for old 'request error' false-invalid records; dry run by default.

Stop application writers before --apply. A new validated SQLite backup is mandatory.
Only counts are printed; no URLs, credentials, or raw messages leave the database.
"""
import argparse
import json
from pathlib import Path
import sqlite3


TRANSPORT_ERROR = "(lower(trim(message)) = 'request error' OR lower(trim(message)) LIKE '%: request error')"
TARGETS = {
    'invalid_links': ('invalid_share_links', TRANSPORT_ERROR),
    'negative_cache': ('share_preview_batch_cache', 'ok = 0 AND ' + TRANSPORT_ERROR),
}


def repair(database: Path, *, apply: bool = False, backup: Path | None = None) -> dict:
    source = database.resolve()
    if not source.is_file():
        raise ValueError('An existing database file is required')
    if apply and (backup is None or backup.resolve() == source or backup.exists()):
        raise ValueError('Apply requires a distinct new backup path')
    mode = 'rw' if apply else 'ro'
    with sqlite3.connect(source.as_uri() + '?mode=' + mode, uri=True) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if apply:
            # Exclusive creation avoids overwriting a backup created by another process.
            with backup.open('xb'):
                pass
            with sqlite3.connect(backup) as saved:
                db.backup(saved)
                if saved.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                    raise RuntimeError('Backup validation failed')
            db.execute('BEGIN IMMEDIATE')
        result = {}
        for name, (table, condition) in TARGETS.items():
            result[name] = db.execute(f'SELECT count(*) FROM {table} WHERE {condition}').fetchone()[0] if table in tables else 0
            if apply and table in tables:
                db.execute(f'DELETE FROM {table} WHERE {condition}')
    return dict(result, applied=apply)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    args = parser.parse_args()
    try:
        result = repair(args.database, apply=args.apply, backup=args.backup)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result))


if __name__ == '__main__':
    main()
