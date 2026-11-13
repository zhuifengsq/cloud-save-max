import json
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.models.drive_account import DriveAccount


@pytest.fixture
def db():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(DriveAccount(id=1, name='test', drive_type='quark', cookie='fake', enabled=True, is_default=True, config_json='{}'))
        session.add(Task(id=1, task_uid='test', task_type='drama', taskname='Show', shareurl='https://pan.quark.cn/s/test', savepath='/Shows/Show', account_name='test', extra_json=json.dumps({'saved_latest': {'episode': 30}, 'custom': 'keep'}), addition_json='{}'))
        session.commit()
        yield session
    engine.dispose()


def folder(fid, name):
    return {'fid': fid, 'file_name': name, 'dir': True, 'size': 0}


def video(fid, name):
    return {'fid': fid, 'file_name': name, 'dir': False, 'size': 1024}


def adapter_for(db, directories):
    adapter = Mock()
    adapter.is_active = True
    def listing(fid):
        assert not db.in_transaction(), 'External listing must not hold a DB transaction'
        data = directories[fid]
        if isinstance(data, Exception):
            raise data
        return {'code': 0, 'data': {'list': data}}
    adapter.ls_dir_complete.side_effect = listing
    return adapter


def run_correction(db, adapter, monkeypatch):
    from app.infrastructure.drive.saved_progress import SavedProgressScanner
    from app.application.use_cases.correct_saved_progress import CorrectSavedProgressUseCase
    scanner = SavedProgressScanner(adapter_factory=lambda plan: adapter)
    return CorrectSavedProgressUseCase(db, scanner=scanner).execute(1)


def test_correction_can_lower_old_progress_and_preserves_other_settings(db, monkeypatch):
    adapter = adapter_for(db, {'0': [folder('shows', 'Shows')], 'shows': [folder('show', 'Show')], 'show': [video('10', 'Show.S01E10.mkv'), video('2', 'Show.S01E02.mkv')]})
    result = run_correction(db, adapter, monkeypatch)
    assert result.previous_episode == 30
    assert result.saved_latest.episode == 10
    db.expire_all()
    extra = json.loads(db.get(Task, 1).extra_json)
    assert extra['saved_latest']['episode'] == 10
    assert extra['custom'] == 'keep'
    adapter.mkdir.assert_not_called()
    adapter.save_file.assert_not_called()
    adapter.rename.assert_not_called()
    adapter.delete.assert_not_called()


@pytest.mark.parametrize('failure', ['listing', 'unrecognized', 'missing_path', 'incomplete', 'cycle'])
def test_failed_or_ambiguous_scan_preserves_saved_progress(db, monkeypatch, failure):
    from app.core.errors import ApiError
    tree = {'0': [folder('shows', 'Shows')], 'shows': [folder('show', 'Show')], 'show': [video('10', 'Show.S01E10.mkv')]}
    if failure == 'listing': tree['show'] = RuntimeError('network failed')
    if failure == 'unrecognized': tree['show'] = [video('unknown', 'Show.mkv')]
    if failure == 'missing_path': tree['shows'] = []
    if failure == 'cycle': tree['show'].append(folder('show', 'Loop'))
    adapter = adapter_for(db, tree)
    if failure == 'incomplete':
        adapter.ls_dir_complete.side_effect = lambda fid: {'code': 1, 'message': 'page 2 failed', 'data': {'list': tree.get(fid, [])}}
    with pytest.raises(ApiError) as error: run_correction(db, adapter, monkeypatch)
    assert error.value.code == {
        'listing': 'SAVED_PROGRESS_READ_FAILED', 'unrecognized': 'SAVED_PROGRESS_UNRECOGNIZED',
        'missing_path': 'SAVED_PROGRESS_PATH_NOT_FOUND', 'incomplete': 'SAVED_PROGRESS_READ_FAILED',
        'cycle': 'SAVED_PROGRESS_SCAN_LIMIT',
    }[failure]
    db.rollback()
    assert json.loads(db.get(Task, 1).extra_json)['saved_latest']['episode'] == 30


def test_complete_empty_directory_clears_incorrect_old_progress(db, monkeypatch):
    adapter = adapter_for(db, {'0': [folder('shows', 'Shows')], 'shows': [folder('show', 'Show')], 'show': []})
    result = run_correction(db, adapter, monkeypatch)
    assert result.saved_latest.episode == 0
    assert json.loads(db.get(Task, 1).extra_json)['saved_latest']['episode'] == 0


def test_recurses_actual_files_and_does_not_use_folder_episode_hint(db, monkeypatch):
    adapter = adapter_for(db, {'0': [folder('shows', 'Shows')], 'shows': [folder('show', 'Show')], 'show': [folder('season', '全40集')], 'season': [video('10', 'Show.S01E10.mp4'), video('ad', '01.jpg')]})
    result = run_correction(db, adapter, monkeypatch)
    assert result.saved_latest.episode == 10
    assert result.scanned_directories == 2


def test_partly_unrecognized_episode_files_do_not_allow_lowering_the_watermark(db, monkeypatch):
    from app.core.errors import ApiError
    adapter = adapter_for(db, {'0': [folder('shows', 'Shows')], 'shows': [folder('show', 'Show')],
        'show': [video('10', 'Show.S01E10.mkv'), video('unknown', 'Show unknown episode.mkv')]})
    with pytest.raises(ApiError) as error:
        run_correction(db, adapter, monkeypatch)
    assert error.value.code == 'SAVED_PROGRESS_UNRECOGNIZED'
    assert json.loads(db.get(Task, 1).extra_json)['saved_latest']['episode'] == 30


def test_unrecognized_error_identifies_the_saved_file_and_relative_directory(db, monkeypatch):
    from app.core.errors import ApiError
    adapter = adapter_for(db, {'0': [folder('shows', 'Shows')], 'shows': [folder('show', 'Show')],
        'show': [folder('quality', '4K DV')], 'quality': [video('10', '10.zip'), video('extra', '番外.zip'), video('trailer', '预告.mp4')]})
    with pytest.raises(ApiError) as error:
        run_correction(db, adapter, monkeypatch)
    assert '4K DV/番外.zip' in error.value.message
    detail = json.loads(error.value.detail)
    assert detail['unrecognized_files'] == ['4K DV/番外.zip', '4K DV/预告.mp4']
    assert detail['unrecognized_count'] == 2
    assert json.loads(db.get(Task, 1).extra_json)['saved_latest']['episode'] == 30


def test_numbered_zip_share_layout_is_recognized_without_renaming(db, monkeypatch):
    tree = {'0': [folder('shows', 'Shows')], 'shows': [folder('show', 'Show')],
        'show': [folder('dv', '4K DV 高码率 60帧率'), folder('sdr', '4K SDR 高码率 60帧率'),
                 folder('low', '4K SDR 低码率 60帧率 更新至20集'), video('advert', '链接每天失效进群.jpg')]}
    for folder_id, count in [('dv', 19), ('sdr', 19), ('low', 20)]:
        tree[folder_id] = [video(f'{folder_id}-{i}', f'{i:02}.zip') for i in range(1, count + 1)]
    result = run_correction(db, adapter_for(db, tree), monkeypatch)
    assert result.saved_latest.episode == 20
    assert result.saved_latest.name == '20.zip'


def test_correction_ignores_confirmed_supplemental_videos_in_numbered_share(db, monkeypatch):
    names = [f'{i:02}.zip' for i in range(1, 17)] + [
        '10(1).zip', '11(1).zip', '12(1).zip', '13(1).zip', '14(1).zip', '15(1).zip',
        '12.mp4', '13.mp4', '14.mp4', '15.mp4',
        '彩蛋1.mp4', '彩蛋2.mp4', '太阳雨角色番综第一期.mp4',
        '2026.11.07-杨超越董思成上演真人版“胡闹厨房”.mp4',
        '2026.11.08-杨超越董思成开启“微醺派对”.mp4',
    ]
    tree = {'0': [folder('shows', 'Shows')], 'shows': [folder('show', 'Show')],
            'show': [video(str(i), name) for i, name in enumerate(names)]}
    result = run_correction(db, adapter_for(db, tree), monkeypatch)
    assert result.saved_latest.episode == 16
    assert result.saved_latest.name == '16.zip'
    assert result.scanned_files == 31
    assert json.loads(db.get(Task, 1).extra_json)['saved_latest']['episode'] == 16


@pytest.mark.parametrize('supplemental', ['彩蛋E99.mp4', '太阳雨角色番综第99期.mp4'])
def test_supplemental_episode_number_does_not_inflate_main_progress(db, monkeypatch, supplemental):
    tree = {'0': [folder('shows', 'Shows')], 'shows': [folder('show', 'Show')],
            'show': [video('16', '16.zip'), video('extra', supplemental)]}
    result = run_correction(db, adapter_for(db, tree), monkeypatch)
    assert result.saved_latest.episode == 16


def test_date_prefix_with_explicit_episode_is_still_counted(db, monkeypatch):
    tree = {'0': [folder('shows', 'Shows')], 'shows': [folder('show', 'Show')],
            'show': [video('16', '16.zip'), video('17', '2026.11.08-Show.S01E17.mp4')]}
    result = run_correction(db, adapter_for(db, tree), monkeypatch)
    assert result.saved_latest.episode == 17


def test_only_supplemental_videos_preserve_previous_progress(db, monkeypatch):
    from app.core.errors import ApiError
    tree = {'0': [folder('shows', 'Shows')], 'shows': [folder('show', 'Show')],
            'show': [video('extra', '彩蛋1.mp4'), video('date', '2026.11.08-微醺派对.mp4')]}
    with pytest.raises(ApiError) as error:
        run_correction(db, adapter_for(db, tree), monkeypatch)
    assert error.value.code == 'SAVED_PROGRESS_UNRECOGNIZED'
    assert json.loads(db.get(Task, 1).extra_json)['saved_latest']['episode'] == 30


def test_unrecognized_diagnostics_bound_samples_but_count_all_files(db, monkeypatch):
    from app.core.errors import ApiError
    tree = {'0': [folder('shows', 'Shows')], 'shows': [folder('show', 'Show')],
            'show': [video(str(i), f'unknown-{chr(97 + i)}.mkv') for i in range(8)]}
    with pytest.raises(ApiError) as error:
        run_correction(db, adapter_for(db, tree), monkeypatch)
    detail = json.loads(error.value.detail)
    assert detail['unrecognized_count'] == 8
    assert detail['unrecognized_files'] == [f'unknown-{chr(97 + i)}.mkv' for i in range(5)]
    assert json.loads(db.get(Task, 1).extra_json)['saved_latest']['episode'] == 30


@pytest.mark.parametrize('change', ['path', 'extra', 'account'])
def test_configuration_changed_during_scan_rejects_writeback(db, monkeypatch, change):
    from app.application.use_cases.correct_saved_progress import CorrectSavedProgressUseCase
    from app.infrastructure.drive.saved_progress import SqlSavedProgressStore
    from app.domain.ports.saved_progress import SavedLatestProgress, SavedProgressScan
    from app.core.errors import ApiError
    class Scanner:
        def scan(self, plan):
            assert not db.in_transaction()
            if change == 'path': db.execute(update(Task).where(Task.id == 1).values(savepath='/other'))
            if change == 'extra': db.execute(update(Task).where(Task.id == 1).values(extra_json='{"saved_latest":{"episode":35}}'))
            if change == 'account': db.execute(update(DriveAccount).where(DriveAccount.id == 1).values(cookie='changed'))
            db.commit()
            return SavedProgressScan(SavedLatestProgress(10, 1, 'Show.S01E10.mkv', 'now'), 1, 1)
    with pytest.raises(ApiError) as error:
        CorrectSavedProgressUseCase(db, store=SqlSavedProgressStore(db), scanner=Scanner()).execute(1)
    assert error.value.http_status == 409
    extra = json.loads(db.get(Task, 1).extra_json)
    assert extra['saved_latest']['episode'] == (35 if change == 'extra' else 30)


def test_task_execution_and_correction_share_admission(db, monkeypatch):
    from app.application.use_cases.correct_saved_progress import CorrectSavedProgressUseCase
    from app.infrastructure.scheduling.task_admission import TaskAdmission
    from app.core.errors import ApiError
    admission = TaskAdmission(4)
    with admission.reserve(1):
        with pytest.raises(ApiError) as error:
            CorrectSavedProgressUseCase(db, admission=admission).execute(1)
    assert error.value.http_status == 409


def test_correction_route_requires_write_permission_and_returns_typed_result(db, monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes import task_saved_progress
    from app.api.deps import get_current_user, CurrentUser
    from app.application.container import get_db
    from app.core.errors import ApiError
    from app.core.exception_handlers import api_error_handler
    from app.domain.ports.saved_progress import SavedProgressCorrection, SavedLatestProgress
    called = []
    class UseCase:
        def execute(self, task_id, **kwargs):
            called.append(task_id)
            return SavedProgressCorrection(task_id, 30, SavedLatestProgress(10, 1, 'Show.S01E10.mkv', 'now'), 10, 1)
    monkeypatch.setattr(task_saved_progress, 'correct_saved_progress_use_case', lambda db: UseCase())
    app = FastAPI()
    app.add_exception_handler(ApiError, api_error_handler)
    app.include_router(task_saved_progress.router, prefix='/tasks')
    app.dependency_overrides[get_db] = lambda: db
    permissions = {'task:read'}
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(SimpleNamespace(id=None), [], permissions)
    with TestClient(app) as client:
        assert client.post('/tasks/1/saved-progress/correct').status_code == 403
        assert called == []
        permissions.add('task:write')
        response = client.post('/tasks/1/saved-progress/correct')
        assert response.status_code == 200, response.text
        assert response.json()['saved_latest']['episode'] == 10
        assert response.json()['previous_episode'] == 30
        assert client.post('/tasks/0/saved-progress/correct').status_code == 422


def test_audit_failure_rolls_back_progress_correction(db, monkeypatch):
    from app.application import audit
    monkeypatch.setattr(audit, 'write_audit_log', Mock(side_effect=RuntimeError('audit unavailable')))
    adapter = adapter_for(db, {'0': [folder('shows', 'Shows')], 'shows': [folder('show', 'Show')], 'show': [video('10', 'Show.S01E10.mkv')]})
    with pytest.raises(RuntimeError, match='audit unavailable'):
        run_correction(db, adapter, monkeypatch)
    assert json.loads(db.get(Task, 1).extra_json)['saved_latest']['episode'] == 30


def test_directory_limit_keeps_old_watermark(db, monkeypatch):
    from app.infrastructure.drive.saved_progress import SavedProgressScanner
    from app.application.use_cases.correct_saved_progress import CorrectSavedProgressUseCase
    from app.core.errors import ApiError
    adapter = adapter_for(db, {'0': [folder('shows', 'Shows')], 'shows': [folder('show', 'Show')], 'show': [folder('season', 'Season 1')], 'season': [video('10', 'Show.S01E10.mkv')]})
    scanner = SavedProgressScanner(adapter_factory=lambda plan: adapter)
    scanner.MAX_DIRECTORIES = 1
    with pytest.raises(ApiError) as error:
        CorrectSavedProgressUseCase(db, scanner=scanner).execute(1)
    assert error.value.code == 'SAVED_PROGRESS_SCAN_LIMIT'
    assert json.loads(db.get(Task, 1).extra_json)['saved_latest']['episode'] == 30


@pytest.mark.parametrize('change', ['account', 'new_default', 'magic'])
def test_late_account_or_rule_change_is_guarded_by_the_update_itself(db, change):
    from app.application.use_cases.correct_saved_progress import CorrectSavedProgressUseCase
    from app.infrastructure.drive.saved_progress import SqlSavedProgressStore
    from app.infrastructure.persistence.models.magic_regex_rule import MagicRegexRule
    from app.domain.ports.saved_progress import SavedLatestProgress, SavedProgressScan
    from app.core.errors import ApiError
    if change == 'new_default':
        db.get(Task, 1).account_name = None
        db.get(DriveAccount, 1).is_default = False
        db.commit()
    class Store(SqlSavedProgressStore):
        calls = 0
        def prepare(self, task_id):
            current = super().prepare(task_id)
            self.calls += 1
            if self.calls == 2:
                # Interleave after validation but before the guarded UPDATE.
                with Session(db.bind) as other:
                    if change == 'account': other.execute(update(DriveAccount).where(DriveAccount.id == 1).values(cookie='new-cookie'))
                    elif change == 'new_default': other.add(DriveAccount(name='new-default', drive_type='quark', cookie='new', enabled=True, is_default=True, config_json='{}'))
                    else: other.add(MagicRegexRule(key='$new', pattern='abc', replace='def', enabled=True))
                    other.commit()
            return current
    class Scanner:
        def scan(self, plan): return SavedProgressScan(SavedLatestProgress(10, 1, 'Show.S01E10.mkv', 'now'), 1, 1)
    with pytest.raises(ApiError) as error:
        CorrectSavedProgressUseCase(db, store=Store(db), scanner=Scanner()).execute(1)
    assert error.value.http_status == 409
    db.expire_all()
    assert json.loads(db.get(Task, 1).extra_json)['saved_latest']['episode'] == 30
