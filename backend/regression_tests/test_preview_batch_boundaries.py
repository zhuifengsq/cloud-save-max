from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.application import share_preview_batch as batch
from app.application.dto.task_browse import SharePreviewBatchIn
from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.drive_account import DriveAccount
import app.infrastructure.persistence.models


@pytest.fixture
def preview_db(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + (tmp_path / 'batch.db').as_posix())
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    monkeypatch.setattr(batch, 'SessionLocal', sessions)
    monkeypatch.setattr(batch.time, 'sleep', lambda _: None)
    batch.cache_clear()
    with sessions() as db:
        db.add_all([DriveAccount(name='preferred', drive_type='quark', cookie='fake', enabled=True, is_default=True),
                    DriveAccount(name='fallback', drive_type='quark', cookie='fake', enabled=True)])
        db.commit()
        yield db
    engine.dispose()
    batch.cache_clear()


def install_manager(monkeypatch, db, events, *, enforce_read_boundary=False):
    class Adapter:
        is_active = True
        def __init__(self, name): self.name = name
        def extract_url(self, url):
            if enforce_read_boundary:
                assert not db.in_transaction(), 'network retained database transaction'
            events.append((self.name, 'extract'))
            return 'id', '', '', []
        def get_stoken(self, *args):
            if self.name == 'preferred':
                return {'message': 'fake login failed'}
            return {'data': {'stoken': 'fake', 'author': {'name': 'author'}}}
        def get_detail(self, *args):
            return {'code': 0, 'data': {'list': [{'fid': 'file', 'file_name': 'one.mp4', 'updated_at': 4}]}}
    adapters = {name: Adapter(name) for name in ('preferred', 'fallback')}
    monkeypatch.setattr(batch, 'DatabaseAccountManager', lambda *a, **kw: SimpleNamespace(
        manager=SimpleNamespace(get_adapter=lambda name: adapters[name])))


def test_account_fallback_result_and_memory_cache_preserve_behavior(preview_db, monkeypatch):
    events = []
    install_manager(monkeypatch, preview_db, events)
    payload = SharePreviewBatchIn(shareurls=[' https://pan.quark.cn/s/fake ', 'https://pan.quark.cn/s/fake'])
    result, changed = batch.preview_share_batch(preview_db, payload)
    assert len(result.items) == 1
    item = result.items[0]
    assert item.ok and item.suggested_account_name == 'fallback'
    assert item.latest_video.name == 'one.mp4' and item.share_author_name == 'author'
    assert events == [('preferred', 'extract'), ('fallback', 'extract')]
    assert changed
    cached, changed = batch.preview_share_batch(preview_db, payload)
    assert cached == result and not changed
    assert len(events) == 2


def test_batch_network_runs_after_read_transaction_ends(preview_db, monkeypatch):
    events = []
    install_manager(monkeypatch, preview_db, events, enforce_read_boundary=True)
    result, _ = batch.preview_share_batch(preview_db, SharePreviewBatchIn(shareurls=['https://pan.quark.cn/s/fake']))
    assert result.items[0].ok


def test_database_negative_cache_short_circuits_io(preview_db, monkeypatch):
    from app.application.share_preview_batch_cache import upsert_preview_batch_cache
    url = 'https://pan.quark.cn/s/dead'
    upsert_preview_batch_cache(preview_db, shareurl=url, drive_type='quark', ok=False,
                              message='分享链接已确认失效', ttl_seconds=3600)
    preview_db.commit()
    def forbidden(*args, **kwargs):
        raise AssertionError('negative cache must not construct adapters')
    monkeypatch.setattr(batch, 'DatabaseAccountManager', forbidden)
    result, _ = batch.preview_share_batch(preview_db, SharePreviewBatchIn(shareurls=[url]))
    assert not result.items[0].ok and result.items[0].message == '分享链接已确认失效'


def test_batch_exception_text_is_not_returned_or_persisted_as_dead_link(preview_db, monkeypatch):
    from app.infrastructure.persistence.models.invalid_share_link import InvalidShareLink
    class Broken:
        is_active = True
        def extract_url(self, url):
            raise RuntimeError('fake-cookie-secret')
    monkeypatch.setattr(batch, 'DatabaseAccountManager', lambda *a, **kw: SimpleNamespace(
        manager=SimpleNamespace(get_adapter=lambda name: Broken())))
    result, _ = batch.preview_share_batch(preview_db, SharePreviewBatchIn(shareurls=['https://pan.quark.cn/s/failure']))
    assert not result.items[0].ok
    assert 'fake-cookie-secret' not in result.items[0].message
    assert preview_db.query(InvalidShareLink).count() == 0


def test_real_transport_failure_is_not_cached_or_marked_invalid(preview_db, monkeypatch):
    import requests
    from app.infrastructure.drive.adapters.quark_adapter import QuarkAdapter
    from app.infrastructure.persistence.models.invalid_share_link import InvalidShareLink
    from app.infrastructure.persistence.models.share_preview_batch_cache import SharePreviewBatchCache
    adapter = QuarkAdapter(cookie='fake-cookie', no_login=True)
    monkeypatch.setattr(batch, 'DatabaseAccountManager', lambda *a, **kw: SimpleNamespace(
        manager=SimpleNamespace(get_adapter=lambda name: adapter)))
    def disconnected(*args, **kwargs):
        raise requests.ConnectionError('fake-transport-secret')
    monkeypatch.setattr(requests, 'request', disconnected)
    payload = SharePreviewBatchIn(shareurls=['https://pan.quark.cn/s/recover'])
    result, _ = batch.preview_share_batch(preview_db, payload)
    assert not result.items[0].ok
    assert preview_db.query(InvalidShareLink).count() == 0
    assert preview_db.query(SharePreviewBatchCache).count() == 0
    preview_db.rollback()
    def recovered(method, url, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response._content = (b'{"status":200,"code":0,"data":{"stoken":"fake"}}' if method == 'POST'
            else b'{"status":200,"code":0,"data":{"list":[]},"metadata":{"_total":0}}')
        return response
    monkeypatch.setattr(requests, 'request', recovered)
    # Keep memory cache intact: transient failures must not suppress recovery.
    result, _ = batch.preview_share_batch(preview_db, payload)
    assert result.items[0].ok


@pytest.mark.parametrize('failed_detail', [
    {'status': 500, 'code': 1, 'message': 'request error'},
    {'status': 200, 'code': 0}, None, {'code': 0, 'data': {'list': 'malformed'}}])
def test_failed_or_malformed_detail_falls_back_instead_of_reporting_success(preview_db, monkeypatch, failed_detail):
    class Adapter:
        is_active = True
        def __init__(self, name): self.name = name
        def extract_url(self, url): return 'fake', '', '', []
        def get_stoken(self, *args): return {'code': 0, 'data': {'stoken': 'fake'}}
        def get_detail(self, *args):
            return failed_detail if self.name == 'preferred' else {'code': 0, 'data': {'list': []}}
    monkeypatch.setattr(batch, 'DatabaseAccountManager', lambda *a, **kw: SimpleNamespace(
        manager=SimpleNamespace(get_adapter=lambda name: Adapter(name))))
    result, _ = batch.preview_share_batch(preview_db, SharePreviewBatchIn(shareurls=['https://pan.quark.cn/s/detail']))
    assert result.items[0].ok
    assert result.items[0].suggested_account_name == 'fallback'


def test_only_confirmed_invalid_token_is_persisted(preview_db, monkeypatch):
    from app.infrastructure.drive.adapters.quark_adapter import QuarkAdapter
    from app.infrastructure.persistence.models.invalid_share_link import InvalidShareLink
    adapter = QuarkAdapter(cookie='fake-cookie', no_login=True)
    monkeypatch.setattr(adapter, 'get_stoken', lambda *a: {'code': 41004, 'message': '分享已失效'})
    monkeypatch.setattr(batch, 'DatabaseAccountManager', lambda *a, **kw: SimpleNamespace(
        manager=SimpleNamespace(get_adapter=lambda name: adapter)))
    result, _ = batch.preview_share_batch(preview_db, SharePreviewBatchIn(shareurls=['https://pan.quark.cn/s/invalid']))
    assert not result.items[0].ok
    assert preview_db.query(InvalidShareLink).count() == 1
