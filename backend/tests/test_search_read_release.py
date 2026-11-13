from types import SimpleNamespace
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from app.application import share_preview_batch as preview


@pytest.mark.parametrize('name', ['fetch_share_file_list_grouped', 'fetch_share_root_dirs'])
def test_preview_releases_read_before_adapter_network(monkeypatch, name):
    with Session(create_engine('sqlite://')) as db:
        events=[]
        class Adapter:
            is_active=False
            def init(self):
                assert not db.in_transaction(); events.append('init'); return True
            def extract_url(self,url):
                assert not db.in_transaction(); return 'id','','',''
            def get_stoken(self,*args):
                assert not db.in_transaction(); events.append('token'); return {'data':{'stoken':'token'}}
            def get_detail(self,*args):
                assert not db.in_transaction(); events.append('detail'); return {'data':{'list':[]}}
        adapter=Adapter()
        def manager(*a,**k):
            db.execute(text('select 1'))
            return SimpleNamespace(get_adapter_for_task=lambda *a,**k:adapter)
        monkeypatch.setattr(preview,'DatabaseAccountManager',manager)
        # Local factory is resolved from container in these functions.
        from app.application import container
        monkeypatch.setattr(container,'account_manager_cls',lambda:manager)
        def release():
            assert db.in_transaction(); events.append('release'); db.rollback()
        getattr(preview,name)(db,'https://pan.quark.cn/s/test',account_name='demo',release_read=release)
        assert events[0]=='release'
        assert 'token' in events

@pytest.mark.parametrize('kind',['api','plugin'])
def test_search_sources_release_before_worker(monkeypatch,kind):
    from app.application import api_search_sources as api, search_plugins as plugins
    with Session(create_engine('sqlite://')) as db:
        calls=[]
        def release():
            calls.append('release');db.rollback()
        def search(*a,**k):
            assert not db.in_transaction();calls.append('network');return []
        db.execute(text('select 1'))
        if kind=='api':
            monkeypatch.setattr(api,'resource_search_sources_repo',lambda:SimpleNamespace(list_enabled_api_sources=lambda *a,**k:[SimpleNamespace(config_json='{}')]))
            monkeypatch.setattr(api,'search_api_source',search)
            result=api.search_enabled_api_sources_result(db,'movie',release_read=release)
        else:
            plugin=SimpleNamespace(timeout_seconds=1,configure=lambda config:None)
            monkeypatch.setattr(plugins,'discover_search_plugins',lambda **k:[{'key':'demo','status':'ready','plugin':plugin}])
            monkeypatch.setattr(plugins,'resource_search_sources_repo',lambda:SimpleNamespace(list_enabled_api_sources=lambda *a,**k:[SimpleNamespace(key='plugin:demo',config_json='{}')]))
            monkeypatch.setattr(plugins,'_run_search',lambda *a,**k:(search(),None))
            result=plugins.search_enabled_plugins_result(db,'movie',release_read=release)
        assert calls==['release','network']
        assert result.succeeded==1


def test_streaming_freezes_accounts_and_releases_before_workers(monkeypatch):
    from app.application import system_settings
    with Session(create_engine('sqlite://')) as db:
        released=False
        class Account:
            id=1;is_default=True;runtime_status='active'
            @property
            def name(self):
                assert not released, 'ORM attribute accessed after release'
                return 'demo'
        class Adapter:
            is_active=True
            def extract_url(self,url):
                assert not db.in_transaction();return 'id','','',''
            def get_stoken(self,*args):
                assert not db.in_transaction();return {'data':{'stoken':'token'}}
            def get_detail(self,*args):
                assert not db.in_transaction();return {'data':{'list':[{'name':'video.mp4'}]}}
        monkeypatch.setattr(preview,'drive_accounts_repo',lambda:SimpleNamespace(list_enabled_by_type=lambda *a,**k:[Account()]))
        monkeypatch.setattr(preview,'DatabaseAccountManager',lambda *a,**k:SimpleNamespace(manager=SimpleNamespace(get_adapter=lambda name:Adapter())))
        monkeypatch.setattr(preview,'_update_validate_cache_ttl',lambda:None)
        monkeypatch.setattr(system_settings,'get_setting',lambda *a:'1')
        preview.cache_clear();db.execute(text('select 1'))
        def release():
            nonlocal released
            db.rollback();released=True
        result=list(preview.validate_share_links_streaming(db,['https://pan.quark.cn/s/test'],release_read=release))
        assert released and result[0].ok
