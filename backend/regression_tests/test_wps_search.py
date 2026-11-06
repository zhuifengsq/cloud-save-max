import json
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from starlette.requests import Request
from importlib import util
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from app.models.resource_search_source import ResourceSearchSource
from app.services import search_plugins


def paragraph(text, href=None):
    node = {'type': 'text', 'text': text}
    if href:
        node['marks'] = [{'type': 'link', 'attrs': {'href': href}}]
    return {'type': 'paragraph', 'content': [node]}


def document():
    return {'type': 'doc', 'content': [
        paragraph('2026.11.2（热门更新）'),
        paragraph('国剧「尚公主更新23/40集」4K'),
        paragraph('百度链接：点击查看', 'https://pan.baidu.com/s/one'),
        paragraph('提取码：1111'),
        paragraph('夸克链接：https://pan.quark.cn/s/two'),
        paragraph('不用打开直接长按复制网盘链接'),
        paragraph('伟大的长征 更43/60集'),
        paragraph('百度链接：https://pan.baidu.com/s/three?pwd=6666'),
        paragraph('提取码：6666'),
        paragraph('夸克链接：https://pan.quark.cn/s/four'),
    ]}


class WpsSearchTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(util.find_spec('app.services.wps_document_search'), 'WPS 搜索服务尚未实现')
        from app.services import wps_document_search as service
        self.service = service
        self.config = service.validate_config({})
        service.clear_cache(self.config['document_url'])

    def test_groups_links_and_passcodes_without_crossing_titles(self):
        rows = self.service.parse_document(document())
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[0]['shareurl'], 'https://pan.baidu.com/s/one?pwd=1111')
        self.assertIn('尚公主', rows[1]['taskname'])
        self.assertIn('伟大的长征', rows[2]['taskname'])
        self.assertNotIn('1111', rows[2]['shareurl'])
        self.assertEqual(rows[0]['datetime'], '2026-11-02')

    def test_search_reuses_document_and_filters_drive_before_results_limit(self):
        with patch.object(self.service, 'fetch_document', return_value=document()) as fetch:
            all_rows = self.service.search('尚公主', self.config)
            quark = self.service.search('尚公主', {**self.config, 'drive_types': ['quark']})
            other = self.service.search('伟大的长征', self.config)
        self.assertEqual(len(all_rows), 2)
        self.assertEqual([row['shareurl'] for row in quark], ['https://pan.quark.cn/s/two'])
        self.assertEqual(len(other), 2)
        self.assertEqual(fetch.call_count, 1)

    def test_clear_forces_next_search_to_fetch_new_data(self):
        changed = {'type': 'doc', 'content': [paragraph('尚公主更新24/40集'), paragraph('https://pan.quark.cn/s/new')]}
        with patch.object(self.service, 'fetch_document', side_effect=[document(), changed]):
            self.service.search('尚公主', self.config)
            self.service.clear_cache(self.config['document_url'])
            rows = self.service.search('尚公主', self.config)
        self.assertEqual([row['shareurl'] for row in rows], ['https://pan.quark.cn/s/new'])

    def test_expiry_refetches_and_bad_response_does_not_become_empty_cache(self):
        with patch.object(self.service, 'fetch_document', return_value=document()):
            self.service.search('尚公主', self.config)
        with patch.object(self.service, 'CACHE_TTL', -1), patch.object(self.service, 'fetch_document', side_effect=ValueError('文档不可读取')):
            with self.assertRaisesRegex(ValueError, '文档不可读取'):
                self.service.search('尚公主', self.config)
        self.assertTrue(self.service.cache_status(self.config['document_url'])['cached'])

    def test_config_rejects_non_wps_urls_and_empty_drive_selection(self):
        for value in ['http://127.0.0.1/', 'https://www.kdocs.cn.evil.example/l/abc', 'https://www.kdocs.cn@evil.example/l/abc']:
            with self.assertRaises(ValueError):
                self.service.validate_config({'document_url': value})
        with self.assertRaises(ValueError):
            self.service.validate_config({'drive_types': []})

    def test_instruction_and_orphan_links_do_not_reuse_previous_title(self):
        data = {'type': 'doc', 'content': [paragraph('https://pan.quark.cn/s/orphan'), paragraph('测试剧更12集'),
                paragraph('https://pan.quark.cn/s/valid'), paragraph('以下是使用说明'), paragraph('https://pan.quark.cn/s/help')]}
        rows = self.service.parse_document(data)
        self.assertEqual([row['shareurl'] for row in rows], ['https://pan.quark.cn/s/valid'])

    def test_plugin_settings_persist_and_apply_to_test_and_runtime_search(self):
        engine = create_engine('sqlite://')
        ResourceSearchSource.__table__.create(engine)
        with Session(engine) as db:
            item = next(p for p in search_plugins.list_search_plugins(db) if p['key'] == 'wps')
            self.assertTrue(item.get('configurable'))
            self.assertEqual(item['config']['document_url'], self.config['document_url'])
            search_plugins.save_search_plugin_config(db, 'wps', {**self.config, 'drive_types': ['baidu']})
            search_plugins.set_search_plugin_enabled(db, 'wps', True)
            db.commit()
            with patch.object(self.service, 'fetch_document', return_value=document()):
                test = search_plugins.test_search_plugin(db, 'wps', '尚公主')
                runtime = search_plugins.search_enabled_plugins(db, '尚公主')
            self.assertTrue(test['success'])
            self.assertEqual([r['shareurl'] for r in test['items']], ['https://pan.baidu.com/s/one?pwd=1111'])
            self.assertEqual([r['shareurl'] for r in runtime], ['https://pan.baidu.com/s/one?pwd=1111'])
            search_plugins.clear_search_plugin_cache(db, 'wps')
            self.assertFalse(self.service.cache_status(self.config['document_url'])['cached'])
        engine.dispose()

    def test_wps_movie_uses_its_own_document_configuration_and_cache(self):
        movie_url = 'https://www.kdocs.cn/l/ctcFh0Mafjjq'
        self.addCleanup(self.service.clear_cache, movie_url)
        self.service.clear_cache(movie_url)
        engine = create_engine('sqlite://')
        self.addCleanup(engine.dispose)
        ResourceSearchSource.__table__.create(engine)
        with Session(engine) as db:
            plugins = {p['key']: p for p in search_plugins.list_search_plugins(db)}
            movie = plugins.get('wps_movie')
            self.assertIsNotNone(movie, '应发现 wps电影 搜索源')
            self.assertEqual(movie['name'], 'wps电影')
            self.assertEqual(movie['config']['document_url'], movie_url)
            self.assertFalse(movie['enabled'])
            self.assertEqual(plugins['wps']['config'], self.config)

            search_plugins.save_search_plugin_config(db, 'wps_movie', {'drive_types': ['quark']})
            search_plugins.set_search_plugin_enabled(db, 'wps_movie', True)
            search_plugins.set_search_plugin_enabled(db, 'wps', True)
            original = self.service.parse_document(document())
            movie_doc = {'type': 'doc', 'content': [paragraph('尚公主更24集'), paragraph('https://pan.quark.cn/s/movie')]}
            docs = {self.config['document_url']: document(), movie_url: movie_doc}
            with patch.object(self.service, 'fetch_document', side_effect=docs.__getitem__) as fetch:
                rows = search_plugins.search_enabled_plugins(db, '尚公主')
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual({row['shareurl'] for row in rows}, {
                original[0]['shareurl'], original[1]['shareurl'], 'https://pan.quark.cn/s/movie',
            })
            search_plugins.clear_search_plugin_cache(db, 'wps_movie')
            self.assertFalse(self.service.cache_status(movie_url)['cached'])
            self.assertTrue(self.service.cache_status(self.config['document_url'])['cached'])

    def test_routes_clear_aggregate_cache_and_keep_saved_config(self):
        from app.api.routes.search_plugins import put_search_plugin_config, post_clear_search_plugin_cache, SearchPluginConfigIn
        from app.services import resource_search
        engine = create_engine('sqlite://')
        ResourceSearchSource.__table__.create(engine)
        request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': []})
        current = SimpleNamespace(roles=['admin'], user=SimpleNamespace(id=1))
        with Session(engine) as db, patch('app.api.routes.search_plugins._audit'):
            resource_search._search_cache['wps-test'] = ([], False, None)
            put_search_plugin_config(request, 'wps', SearchPluginConfigIn(config={**self.config, 'drive_types': ['quark']}), current, db)
            self.assertNotIn('wps-test', resource_search._search_cache)
            with patch.object(self.service, 'fetch_document', return_value=document()):
                self.service.search('尚公主', self.config)
            resource_search._search_cache['wps-test'] = ([], False, None)
            post_clear_search_plugin_cache(request, 'wps', current, db)
            self.assertNotIn('wps-test', resource_search._search_cache)
            self.assertFalse(self.service.cache_status(self.config['document_url'])['cached'])
            row = db.query(ResourceSearchSource).filter_by(key='plugin:wps').one()
            self.assertEqual(json.loads(row.config_json)['drive_types'], ['quark'])
        engine.dispose()

    def test_non_admin_cannot_change_config_or_clear_cache(self):
        from app.api.routes.search_plugins import put_search_plugin_config, post_clear_search_plugin_cache, SearchPluginConfigIn
        from app.core.errors import ApiError
        request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': []})
        current = SimpleNamespace(roles=[], user=SimpleNamespace(id=2))
        with self.assertRaises(ApiError) as error:
            put_search_plugin_config(request, 'wps', SearchPluginConfigIn(config={}), current, None)
        self.assertEqual(error.exception.http_status, 403)
        with self.assertRaises(ApiError):
            post_clear_search_plugin_cache(request, 'wps', current, None)


if __name__ == '__main__':
    unittest.main()
