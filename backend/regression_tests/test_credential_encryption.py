import base64
import json
import os
import sqlite3

import pytest
from sqlalchemy import create_engine, text, select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.drive_account import DriveAccount


@pytest.fixture
def encrypted_db(tmp_path, monkeypatch):
    keyring = tmp_path / 'keys.json'
    keyring.write_text(json.dumps({'database_id':'fake-db', 'active_key_id':'A',
                                 'keys': {'A':base64.b64encode(os.urandom(32)).decode()}}))
    monkeypatch.setenv('CREDENTIAL_KEYRING_FILE', str(keyring))
    engine=create_engine('sqlite:///' + (tmp_path/'vault.db').as_posix())
    Base.metadata.create_all(engine)
    yield engine, keyring
    engine.dispose()


def test_probe_profile_and_errors_do_not_bypass_encryption(encrypted_db, monkeypatch):
    from app.application import drive_accounts as service
    from types import SimpleNamespace
    engine, _ = encrypted_db
    adapter = SimpleNamespace(init=lambda: True,
        export_runtime_config=lambda: {'cookie': 'fake-cookie'},
        get_account_config=lambda: {'nickname': 'safe', 'used_space': 12,
            'raw': {'account_info': {'access_token': 'fake-profile-secret'}},
            'access_token': 'fake-top-secret'})
    monkeypatch.setattr(service.AdapterFactory, 'create_adapter', lambda *a, **kw: adapter)
    with Session(engine) as db:
        row = service.create_drive_account(db, name='profile', drive_type='quark', config={'cookie': 'fake-cookie'})
        db.commit()
        service.probe_drive_account(db, row.id)
        db.commit()
        raw = db.execute(text('select profile_json from drive_accounts')).scalar_one()
        assert 'secret' not in raw
        assert json.loads(raw)['used_space'] == 12
        def broken():
            raise RuntimeError('URL?token=fake-error-secret')
        adapter.init = broken
        service.probe_drive_account(db, row.id)
        db.commit()
        assert 'fake-error-secret' not in (row.last_error or '')
        assert row.runtime_status == 'error'


@pytest.mark.parametrize('first_change', [{'config': {'password': 'fake-new'}}, {'clear_credentials': ['password']}])
@pytest.mark.parametrize('encrypted', [False, True])
def test_stale_public_edit_cannot_overwrite_or_restore_credentials(encrypted_db, monkeypatch, first_change, encrypted):
    from app.application import drive_accounts as service
    from app.core.errors import ApiError
    engine, _ = encrypted_db
    if not encrypted:
        monkeypatch.delenv('CREDENTIAL_KEYRING_FILE')
    with Session(engine) as seed:
        row = service.create_drive_account(seed, name='race', drive_type='123pan',
            config={'username': 'fake', 'password': 'fake-old'})
        seed.commit()
        identity = row.id
    with Session(engine) as first, Session(engine) as stale:
        first_row = first.get(DriveAccount, identity)
        stale_row = stale.get(DriveAccount, identity)
        assert first_row is not None and stale_row is not None
        service.update_drive_account(first, identity, **first_change)
        first.commit()
        with pytest.raises(ApiError) as error:
            service.update_drive_account(stale, identity, config={'debug': True})
            stale.commit()
        assert error.value.http_status == 409
        stale.rollback()
    with Session(engine) as verify:
        config = json.loads(verify.get(DriveAccount, identity).config_json)
        assert config['password'] == ('fake-new' if 'config' in first_change else '')


def test_explicit_migration_scrubs_legacy_profile_even_for_encrypted_rows(encrypted_db):
    from app.infrastructure.persistence.credential_migration import migrate_credentials
    engine, _ = encrypted_db
    with Session(engine) as db:
        row = DriveAccount(name='history', drive_type='quark', cookie='fake-cookie', config_json='{}')
        db.add(row)
        db.commit()
        # Seed the old storage layout without going through new mapper safeguards.
        db.execute(text('UPDATE drive_accounts SET profile_json=:p,last_error=:e'),
            {'p': json.dumps({'nickname': 'kept', 'raw': {'token': 'fake-historic-secret'}}),
             'e': 'fake-historic-error-secret'})
        db.commit()
        assert migrate_credentials(db) == 1
        db.commit()
        raw = db.execute(text('select profile_json,last_error from drive_accounts')).one()
        assert 'secret' not in str(raw)
        assert json.loads(raw.profile_json)['nickname'] == 'kept'
        assert migrate_credentials(db) == 0


def test_legacy_bulk_update_cannot_bypass_account_versioning(encrypted_db, monkeypatch):
    from sqlalchemy import update
    from app.core.credential_crypto import CredentialError
    engine, _ = encrypted_db
    monkeypatch.delenv('CREDENTIAL_KEYRING_FILE')
    with Session(engine) as db:
        db.add(DriveAccount(name='legacy-version', drive_type='quark', cookie='fake-cookie'))
        db.commit()
        with pytest.raises(CredentialError):
            db.execute(update(DriveAccount).values(_stored_cookie='unversioned'))
        db.rollback()


def test_write_read_expire_rollback_and_raw_database_has_no_secret(encrypted_db):
    engine,_=encrypted_db
    with Session(engine) as db:
        row=DriveAccount(name='fake',drive_type='quark',cookie='fake-cookie',config_json='{"cookie":"fake-cookie"}')
        db.add(row);db.commit()
        assert row.cookie=='fake-cookie'
        assert json.loads(row.config_json)['cookie']=='fake-cookie'
        raw=db.execute(text('select cookie,config_json,credential_blob from drive_accounts')).one()
        assert 'fake-cookie' not in str(raw)
        row.cookie='fake-new';row.config_json='{"cookie":"fake-new"}'
        db.flush();db.expire(row)
        assert row.cookie=='fake-new'
        db.rollback()
        assert row.cookie=='fake-cookie'


def test_tamper_and_missing_key_never_fall_back(encrypted_db, monkeypatch):
    engine,keyring=encrypted_db
    with Session(engine) as db:
        row=DriveAccount(name='fake',drive_type='quark',cookie='fake-cookie',config_json='{}')
        db.add(row);db.commit();identity=row.id
    monkeypatch.delenv('CREDENTIAL_KEYRING_FILE')
    with Session(engine) as db:
        row=db.get(DriveAccount,identity)
        with pytest.raises(Exception, match='Credential'):
            _=row.cookie


def test_blob_copy_corruption_and_wrong_key_are_rejected(encrypted_db):
    from app.core.credential_crypto import CredentialError
    engine,keyring=encrypted_db
    with Session(engine) as db:
        a=DriveAccount(name='a',drive_type='quark',cookie='fake-a',config_json='{}')
        b=DriveAccount(name='b',drive_type='quark',cookie='fake-b',config_json='{}')
        db.add_all([a,b]);db.commit()
        original=b.credential_blob
        b.credential_blob=a.credential_blob
        with pytest.raises(CredentialError):
            _=b.cookie
        b.credential_blob=original[:-2]+'xx'
        with pytest.raises(CredentialError):
            _=b.cookie
        db.rollback()
        ring=json.loads(keyring.read_text());ring['keys']['A']=base64.b64encode(os.urandom(32)).decode()
        keyring.write_text(json.dumps(ring))
        with pytest.raises(CredentialError):
            _=a.cookie


def test_public_config_and_refresh_preserve_expected_values(encrypted_db):
    engine,_=encrypted_db
    with Session(engine) as db:
        row=DriveAccount(name='test',drive_type='123pan',cookie='fake',config_json='{"password":"fake-secret","debug":true,"unknown":"fake-unknown"}')
        db.add(row);db.commit()
        assert json.loads(row._stored_config_json)=={'debug':True}
        row.cookie='unflushed'
        db.refresh(row)
        assert row.cookie=='fake'
        row.name='renamed';db.commit()
        assert json.loads(row.config_json)['password']=='fake-secret'


def test_encrypted_orm_bulk_update_cannot_bypass_credential_protection(encrypted_db, monkeypatch):
    from sqlalchemy import update
    from app.core.credential_crypto import CredentialError
    engine, _ = encrypted_db
    with Session(engine) as db:
        row = DriveAccount(name='protected', drive_type='quark', cookie='fake', config_json='{}')
        db.add(row); db.commit()
        with pytest.raises(CredentialError):
            db.execute(update(DriveAccount).values(cookie='plaintext-bypass'))
        monkeypatch.delenv('CREDENTIAL_KEYRING_FILE')
        with pytest.raises(CredentialError):
            db.execute(update(DriveAccount).values(cookie='missing-key-bypass'))


def test_required_encryption_without_key_rejects_write(tmp_path,monkeypatch):
    monkeypatch.delenv('CREDENTIAL_KEYRING_FILE',raising=False)
    monkeypatch.setenv('CREDENTIAL_ENCRYPTION_REQUIRED','1')
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(DriveAccount(name='fake',drive_type='quark',cookie='fake',config_json='{}'))
        with pytest.raises(Exception,match='Credential'):
            db.commit()
    engine.dispose()


def test_migration_idempotence_rotation_restore_and_aad_rejection(encrypted_db, monkeypatch, tmp_path):
    from app.infrastructure.persistence.credential_migration import migrate_credentials
    engine,keyring=encrypted_db
    ring=json.loads(keyring.read_text())
    monkeypatch.delenv('CREDENTIAL_KEYRING_FILE')
    with Session(engine) as db:
        db.add(DriveAccount(name='legacy',drive_type='quark',cookie='fake-old',config_json='{"cookie":"fake-old"}'))
        db.commit()
    monkeypatch.setenv('CREDENTIAL_KEYRING_FILE',str(keyring))
    with Session(engine) as db, db.begin():
        assert migrate_credentials(db)==1
        assert migrate_credentials(db)==0
    with engine.connect() as connection:
        old_blob=connection.execute(text('select credential_blob from drive_accounts')).scalar()
    ring['keys']['B']=base64.b64encode(os.urandom(32)).decode()
    ring['active_key_id']='B'
    keyring.write_text(json.dumps(ring))
    with Session(engine) as db, db.begin():
        assert migrate_credentials(db,rotate=True)==1
    backup=tmp_path/'restore.db'
    with sqlite3.connect(engine.url.database) as src, sqlite3.connect(backup) as dst:
        src.backup(dst)
        assert dst.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    restored=create_engine('sqlite:///'+backup.as_posix())
    with Session(restored) as db:
        row=db.scalar(select(DriveAccount))
        assert row.cookie=='fake-old'
        assert row.credential_blob!=old_blob
        assert json.loads(row.credential_blob)['key_id']=='B'
        row.credential_uuid='different-account'
        with pytest.raises(Exception,match='Credential'):
            _=row.cookie
        db.rollback()
        assert row.cookie=='fake-old'
    restored.dispose()


@pytest.mark.parametrize('legacy_config', [None, ''])
def test_legacy_cookie_only_migration_keeps_runtime_adapter_configuration(encrypted_db, monkeypatch, legacy_config):
    from app.infrastructure.persistence.credential_migration import migrate_credentials
    from app.infrastructure.drive.adapter_registry import AdapterRegistry
    from app.infrastructure.drive.account_manager import load_accounts_payload, clear_account_snapshot_cache
    engine, keyring = encrypted_db
    monkeypatch.delenv('CREDENTIAL_KEYRING_FILE')
    with Session(engine) as db:
        db.add(DriveAccount(name='old', drive_type='quark', cookie='fake-legacy', config_json=legacy_config))
        db.commit()
        before = AdapterRegistry.parse_config_json('quark', legacy_config, 'fake-legacy')
    monkeypatch.setenv('CREDENTIAL_KEYRING_FILE', str(keyring))
    with Session(engine) as db, db.begin():
        assert migrate_credentials(db) == 1
    clear_account_snapshot_cache()
    with Session(engine) as db:
        row = db.scalar(select(DriveAccount))
        assert AdapterRegistry.parse_config_json('quark', row.config_json, row.cookie) == before
        assert load_accounts_payload(db)['accounts'][0]['config'] == before
        assert before['cookie'] == 'fake-legacy'


@pytest.mark.parametrize('encrypted', [False, True])
@pytest.mark.parametrize('concurrent_update', [False, True])
def test_saved_progress_cas_uses_stored_credential_identity(encrypted_db, monkeypatch, encrypted, concurrent_update):
    from app.core.errors import ApiError
    from app.domain.ports.saved_progress import SavedLatestProgress, SavedProgressScan
    from app.infrastructure.persistence.models.task import Task
    from app.infrastructure.drive.saved_progress import SqlSavedProgressStore
    engine, _ = encrypted_db
    if not encrypted:
        monkeypatch.delenv('CREDENTIAL_KEYRING_FILE')
    with Session(engine) as db:
        account = DriveAccount(name='cas', drive_type='quark', cookie='fake-old', config_json='{}', enabled=True)
        task = Task(task_uid='cas', task_type='drama', taskname='show', shareurl='https://pan.quark.cn/s/fake', savepath='/show', account_name='cas', addition_json='{}', extra_json='{}')
        db.add_all([account, task]); db.commit()
        task_id, account_id = task.id, account.id
        store = SqlSavedProgressStore(db)
        plan = store.prepare(task_id)
        db.commit()
        if concurrent_update:
            with Session(engine) as other:
                changed = other.get(DriveAccount, account_id)
                changed.cookie = 'fake-new'
                other.commit()
            with pytest.raises(ApiError) as error:
                store.save(plan, SavedProgressScan(SavedLatestProgress(1, 1, 'one.mkv', 'now'), 1, 1))
            assert error.value.code == 'TASK_CHANGED'
            db.rollback()
        else:
            store.save(plan, SavedProgressScan(SavedLatestProgress(1, 1, 'one.mkv', 'now'), 1, 1))
            db.commit()
            assert json.loads(db.get(Task, task_id).extra_json)['saved_latest']['episode'] == 1
