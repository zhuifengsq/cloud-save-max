from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.application import share_preview
from app.application.dto.task_browse import SharePreviewIn
from app.core.errors import ApiError
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.drive_account import DriveAccount
from app.infrastructure.drive.share_preview_reader import AdapterFactory
import app.infrastructure.persistence.models


@pytest.fixture
def preview(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + (tmp_path / 'preview.db').as_posix())
    Base.metadata.create_all(engine)
    db = Session(engine)
    adapter = SimpleNamespace(
        extract_url=lambda url: ('id', '', '', []),
        get_stoken=lambda *args: {'data': {'stoken': 'fake'}},
        get_detail=lambda *args: {'code': 0, 'data': {'list': [
            {'fid': 'one', 'file_name': 'episode.mkv', 'size': 100},
            {'fid': 'dir', 'file_name': 'Folder', 'dir': True},
        ]}},
    )
    db.add(DriveAccount(name='fake', drive_type='quark', cookie='fake-cookie', enabled=True, is_default=True, config_json='{}'))
    db.commit()
    monkeypatch.setattr(AdapterFactory, 'create_adapter', lambda *args, **kwargs: adapter)
    yield lambda **kwargs: share_preview.preview_share(SharePreviewIn(shareurl='https://pan.quark.cn/s/fake', **kwargs), db), adapter, db
    db.close()
    engine.dispose()


def test_file_directory_and_missing_optional_author(preview):
    run, _, _ = preview
    result = run()
    assert [(item.name, item.is_dir) for item in result.items] == [('episode.mkv', False), ('Folder', True)]
    assert result.share_author_name is None


def test_rename_and_filters_keep_existing_semantics(preview):
    run, _, _ = preview
    result = run(pattern='episode', replace='renamed', folder_filter='keep', min_size='1KB')
    assert result.items[0].filtered_by_size is True
    assert result.items[0].file_name_re is None
    assert result.items[1].filtered_by_folder == '不包含筛选词'


def test_rename_and_keyword_filter(preview):
    run, _, _ = preview
    assert run(pattern='episode', replace='renamed').items[0].name_re == 'renamed.mkv'
    assert run(filter_words='episode').items[0].filtered_by_keyword is True


def test_invalid_share_is_explicit(preview):
    run, adapter, _ = preview
    adapter.get_stoken = lambda *args: {'code': 1, 'message': 'expired'}
    with pytest.raises(ApiError) as error:
        run()
    assert error.value.code == 'TASK_SHARE_TOKEN_FAILED'


def test_failed_detail_is_explicit(preview):
    run, adapter, _ = preview
    adapter.get_detail = lambda *args: {'code': 1, 'data': {'list': []}}
    with pytest.raises(ApiError) as error:
        run()
    assert error.value.code == 'TASK_SHARE_DETAIL_FAILED'


def test_error_status_cannot_masquerade_as_empty_share(preview):
    run, adapter, _ = preview
    adapter.get_detail = lambda *args: {'status': 500, 'data': {'list': []}}
    with pytest.raises(ApiError) as error:
        run()
    assert error.value.code == 'TASK_SHARE_DETAIL_FAILED'


def test_network_stage_has_no_database_transaction(preview):
    run, adapter, db = preview
    db.execute(text('SELECT 1'))
    def detail(*args):
        assert not db.in_transaction()
        return {'code': 0, 'data': {'list': []}}
    adapter.get_detail = detail
    assert run().items == []


def test_preview_does_not_commit_callers_pending_writes(preview):
    run, _, db = preview
    db.add(DriveAccount(name='pending', drive_type='quark', cookie='fake', enabled=True, config_json='{}'))
    with pytest.raises(RuntimeError, match='read-only'):
        run()
    assert db.new


def test_upstream_exception_is_safe_api_error(preview):
    run, adapter, _ = preview
    def detail(*args):
        raise RuntimeError('fake-cookie-secret')
    adapter.get_detail = detail
    with pytest.raises(ApiError) as error:
        run()
    assert error.value.code == 'TASK_SHARE_DETAIL_FAILED'
    assert 'fake-cookie' not in error.value.message


def test_existing_destination_file_is_marked_saved_and_io_releases_read_transaction(preview):
    run, adapter, db = preview
    def initialized(**kwargs):
        assert not db.in_transaction()
        return True
    adapter.ensure_initialized = initialized
    adapter.get_fids = lambda paths: [{'fid': 'dest', 'file_path': '/saved'}]
    adapter.ls_dir = lambda *args, **kwargs: {'code': 0, 'data': {'list': [{'fid': 'old', 'file_name': 'episode.mkv'}]}}
    result = run(savepath='/saved')
    assert result.items[0].file_name_saved == 'episode.mkv'
    assert result.items[0].file_name_re is None


def test_destination_credentials_and_listing_failure_are_not_silent(preview):
    run, adapter, _ = preview
    adapter.ensure_initialized = lambda **kwargs: False
    with pytest.raises(ApiError) as error:
        run(savepath='/saved')
    assert error.value.code == 'DRIVE_ACCOUNT_UNAVAILABLE'
    adapter.ensure_initialized = lambda **kwargs: True
    adapter.get_fids = lambda paths: [{'fid': 'dest'}]
    adapter.ls_dir = lambda *args, **kwargs: {'code': 1, 'message': 'fake-cookie-secret'}
    with pytest.raises(ApiError) as error:
        run(savepath='/saved')
    assert error.value.code == 'TASK_SHARE_DETAIL_FAILED'
    assert 'fake-cookie' not in error.value.message


def test_root_destination_keeps_existing_empty_path_semantics(preview):
    run, adapter, _ = preview
    # Historically rstrip('/') normalizes '/' to empty, so no own-drive IO.
    result = run(savepath='/')
    assert result.items[0].file_name_re == 'episode.mkv'


def test_invalid_url_and_unknown_account_are_explicit(preview):
    _, _, db = preview
    for payload, code in [
        (SharePreviewIn(shareurl='https://example.invalid/fake'), 'TASK_SHAREURL_INVALID'),
        (SharePreviewIn(shareurl='https://pan.quark.cn/s/fake', account_name='missing'), 'DRIVE_ACCOUNT_NOT_FOUND'),
    ]:
        with pytest.raises(ApiError) as error:
            share_preview.preview_share(payload, db)
        assert error.value.code == code


def test_pure_plan_rename_conflict_filter_and_readonly_inputs():
    from types import MappingProxyType
    from app.application.share_preview_plan import PreviewPlanOptions, plan_preview_items
    from app.infrastructure.execution.magic_rename import MagicRename
    raw = tuple(MappingProxyType(row) for row in [
        {'fid': 'small', 'file_name': 'one.mkv', 'size': 100},
        {'fid': 'large', 'file_name': 'two.mkv', 'size': 200},
        {'fid': 'skip', 'file_name': 'skip.mkv', 'size': 500},
    ])
    options = PreviewPlanOptions(pattern=r'.*\.mkv$', replace='same.mkv', filter_words='skip')
    result = plan_preview_items(options, raw, (), MagicRename(magic_variable={'{TASKNAME}': ''}))
    assert result[0].file_name_saved == '重命名冲突（保留最大）'
    assert result[1].name_re == 'same.mkv'
    assert result[2].filtered_by_keyword is True
    assert 'file_name_re' not in raw[0]


def test_web_and_telegram_share_the_same_preview_behavior(preview):
    from app.api.routes.tasks_share import post_share_preview
    from app.application.telegram_bot.actions import TelegramBotActions
    _, _, db = preview
    payload = SharePreviewIn(shareurl='https://pan.quark.cn/s/fake', pattern='episode', replace='new')
    web = post_share_preview(payload, db).model_dump(mode='json')
    telegram = TelegramBotActions(db=db).preview_task_share(shareurl=payload.shareurl, pattern='episode', replace='new')
    assert telegram['drive_type'] == web['drive_type']
    assert {item['fid']: item['file_name_re'] for item in telegram['items']} == {
        item['fid']: item['file_name_re'] for item in web['items']}
