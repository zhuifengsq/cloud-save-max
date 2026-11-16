import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application import drive_accounts as service
from app.core.errors import ApiError
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.drive_account import DriveAccount
import app.infrastructure.persistence.models


def test_migration_repairs_duplicates_and_keeps_unknown_profile_time(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from pathlib import Path
    backend = Path(__file__).resolve().parents[1]
    database_url = 'sqlite:///' + (tmp_path / 'migration.db').as_posix()
    monkeypatch.setenv('XXM_TESTING', '1')
    monkeypatch.setenv('DATABASE_URL', database_url)
    cfg = Config()
    cfg.set_main_option('script_location', str(backend / 'alembic'))
    command.upgrade(cfg, '20261116_account_row_version')
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO drive_accounts (id,name,drive_type,cookie,config_json,is_default,profile_json,last_checked_at) VALUES (1,'a','quark','fake-a','{}',1,'{}','2020-01-01'),(2,'b','quark','fake-b','{}',1,'{}','2020-01-01')"))
        command.upgrade(cfg, 'head')
        with engine.connect() as connection:
            rows = connection.execute(text('SELECT id,is_default,row_version,profile_updated_at,cookie FROM drive_accounts ORDER BY id')).all()
            assert rows == [(1, 1, 1, None, 'fake-a'), (2, 0, 2, None, 'fake-b')]
            context = MigrationContext.configure(connection, opts={'compare_type': True,
                'include_object': lambda obj, name, kind, reflected, other: kind != 'table' or name == 'drive_accounts'})
            assert compare_metadata(context, Base.metadata) == []
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(text('UPDATE drive_accounts SET is_default=1 WHERE id=2'))
        command.downgrade(cfg, '20261116_account_row_version')
        with engine.connect() as connection:
            assert 'profile_updated_at' not in {row[1] for row in connection.execute(text('PRAGMA table_info(drive_accounts)'))}
            assert connection.execute(text('SELECT count(*) FROM drive_accounts')).scalar_one() == 2
        command.upgrade(cfg, 'head')
        assert default_ids(engine) == [1]
    finally:
        engine.dispose()


@pytest.fixture
def accounts(tmp_path):
    engine = create_engine('sqlite:///' + (tmp_path / 'fake.db').as_posix())
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        a = service.create_drive_account(db, name='a', drive_type='quark', config={'cookie': 'fake-a'})
        b = service.create_drive_account(db, name='b', drive_type='quark', config={'cookie': 'fake-b'})
        db.commit()
        identities = a.id, b.id
    yield engine, identities
    engine.dispose()


def default_ids(engine):
    with Session(engine) as db:
        return list(db.scalars(select(DriveAccount.id).where(DriveAccount.is_default.is_(True))))


def test_stale_concurrent_switch_never_commits_two_defaults(accounts):
    engine, (aid, bid) = accounts
    with Session(engine) as first, Session(engine) as stale:
        # Hold both snapshots, including false flags, across the first commit.
        first_rows = list(first.scalars(select(DriveAccount)))
        stale_rows = list(stale.scalars(select(DriveAccount)))
        assert len(first_rows) == len(stale_rows) == 2
        service.set_default_drive_account(first, aid)
        first.commit()
        try:
            service.set_default_drive_account(stale, bid)
            stale.commit()
        except ApiError as error:
            assert error.http_status == 409
            stale.rollback()
        assert len(default_ids(engine)) == 1


def test_database_rejects_duplicate_default_even_for_direct_sql(accounts):
    engine, (aid, bid) = accounts
    with engine.begin() as connection:
        connection.execute(text('UPDATE drive_accounts SET is_default=1 WHERE id=:id'), {'id': aid})
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(text('UPDATE drive_accounts SET is_default=1 WHERE id=:id'), {'id': bid})
    assert default_ids(engine) == [aid]


def test_switch_creation_and_update_work_with_existing_default(accounts):
    engine, (aid, bid) = accounts
    with Session(engine) as db:
        service.set_default_drive_account(db, bid)
        db.commit()
        # Moving to a lower id must clear the previous row before setting true.
        service.set_default_drive_account(db, aid)
        db.commit()
        assert default_ids(engine) == [aid]
        created = service.create_drive_account(db, name='new', drive_type='quark',
            config={'cookie': 'fake-new'}, is_default=True)
        db.commit()
        created_id = created.id
        assert default_ids(engine) == [created_id]
        service.update_drive_account(db, bid, name='renamed', is_default=True)
        db.commit()
        assert default_ids(engine) == [bid]
        assert db.get(DriveAccount, bid).name == 'renamed'


def test_default_switch_and_other_edits_roll_back_together(accounts):
    engine, (aid, bid) = accounts
    with Session(engine) as db:
        service.set_default_drive_account(db, aid)
        db.commit()
        service.update_drive_account(db, bid, config={'cookie': 'fake-pending'}, is_default=True)
        db.rollback()
    assert default_ids(engine) == [aid]
    with Session(engine) as db:
        assert db.get(DriveAccount, bid).cookie == 'fake-b'


@pytest.mark.parametrize('profile_failure', [False, True])
def test_profile_timestamp_only_advances_when_profile_is_written(accounts, monkeypatch, profile_failure):
    engine, (aid, _) = accounts
    old_time = datetime.now() - timedelta(days=30)
    def profile():
        if profile_failure:
            raise RuntimeError('fake-network-failure')
        return {'nickname': 'fresh', 'used_space': 456}
    adapter = SimpleNamespace(init=lambda: True, export_runtime_config=lambda: {'cookie': 'fake-a'},
                              get_account_config=profile)
    monkeypatch.setattr(service.AdapterFactory, 'create_adapter', lambda *a, **kw: adapter)
    with Session(engine) as db:
        row = db.get(DriveAccount, aid)
        row.profile_json = json.dumps({'nickname': 'old', 'used_space': 123})
        row.last_checked_at = old_time
        row.profile_updated_at = old_time
        db.commit()
        service.probe_drive_account(db, aid)
        db.commit()
        body = service.serialize_drive_account(row)
        assert body['last_checked_at'] > service.normalize_api_datetime(old_time)
        if profile_failure:
            assert body['profile_updated_at'] == service.normalize_api_datetime(old_time)
            assert body['profile']['nickname'] == 'old'
        else:
            assert body['profile_updated_at'] > service.normalize_api_datetime(old_time)
            assert body['profile']['nickname'] == 'fresh'


def test_unknown_historical_profile_time_remains_unknown_after_failed_probe(accounts, monkeypatch):
    engine, (aid, _) = accounts
    adapter = SimpleNamespace(init=lambda: False)
    monkeypatch.setattr(service.AdapterFactory, 'create_adapter', lambda *a, **kw: adapter)
    with Session(engine) as db:
        row = db.get(DriveAccount, aid)
        row.profile_json = json.dumps({'nickname': 'historical'})
        db.commit()
        service.probe_drive_account(db, aid)
        db.commit()
        assert service.serialize_drive_account(row)['profile_updated_at'] is None


def test_successful_profile_time_is_utc_even_on_non_utc_host(accounts, monkeypatch):
    engine, (aid, _) = accounts
    class LocalClock(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)
            return datetime.now(tz)
    monkeypatch.setattr(service, 'datetime', LocalClock)
    adapter = SimpleNamespace(init=lambda: True, export_runtime_config=lambda: {'cookie': 'fake-a'},
                              get_account_config=lambda: {'nickname': 'fresh'})
    monkeypatch.setattr(service.AdapterFactory, 'create_adapter', lambda *a, **kw: adapter)
    before = datetime.now(timezone.utc)
    with Session(engine) as db:
        row = service.probe_drive_account(db, aid)
        db.commit()
        body = service.serialize_drive_account(row)
        after = datetime.now(timezone.utc)
        assert before <= body['profile_updated_at'] <= after
        assert before <= body['last_checked_at'] <= after
