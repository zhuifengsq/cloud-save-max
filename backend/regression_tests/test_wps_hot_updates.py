import unittest
from unittest.mock import patch

from app.application import wps_document_search as wps


def paragraph(text, href=None):
    node = {'type': 'text', 'text': text}
    if href:
        node['marks'] = [{'type': 'link', 'attrs': {'href': href}}]
    return {'type': 'paragraph', 'content': [node]}


def document(*blocks):
    return {'type': 'doc', 'content': [{'type': 'logic_block', 'content': [
        {'type': 'block_tile', 'content': [block]} for block in blocks
    ]}]}


RULE = {'type': 'horizontal_rule'}
HEADLINE = paragraph('📢 2026 年 11 月 5 日热门更新')


class WpsHotUpdatesTests(unittest.TestCase):
    def test_cache_expires_15_minutes_after_fetch_even_when_reused(self):
        url = 'https://www.kdocs.cn/l/cacheTtl'
        content = document(HEADLINE, RULE, paragraph('测试剧【更4集】'), RULE)
        with patch.object(wps, 'fetch_document', return_value=content) as fetch, patch.object(wps.time, 'monotonic') as now:
            now.return_value = 100
            first = wps.read_hot_updates(url)
            now.return_value = 999
            self.assertEqual(wps.read_hot_updates('https://kdocs.cn/l/cacheTtl?from=share'), first)
            fetch.assert_called_once_with(url)
            now.return_value = 1000
            self.assertEqual(wps.read_hot_updates(url), first)
            self.assertEqual(fetch.call_count, 2)

    def test_clearing_one_url_preserves_other_documents_and_refetches(self):
        url = 'https://www.kdocs.cn/l/cacheClear'
        other = 'https://www.kdocs.cn/l/cacheOther'
        before = document(HEADLINE, RULE, paragraph('测试剧【更4集】'), RULE)
        after = document(HEADLINE, RULE, paragraph('测试剧【更5集】'), RULE)
        with patch.object(wps, 'fetch_document', side_effect=[before, before, after]) as fetch:
            wps.read_hot_updates(url)
            wps.read_hot_updates(other)
            wps.clear_hot_updates_cache(url)
            self.assertEqual(wps.read_hot_updates(other)['entries'][0]['title'], '测试剧【更4集】')
            self.assertEqual(wps.read_hot_updates(url)['entries'][0]['title'], '测试剧【更5集】')
            self.assertEqual(fetch.call_count, 3)

    def test_uses_newest_date_and_stops_at_next_rule(self):
        content = document(
            paragraph('说明'), RULE, HEADLINE, RULE,
            paragraph('折月亮 (2026)【更4集】'),
            paragraph('百度链接：点击查看', 'https://pan.baidu.com/s/one'),
            paragraph('提取码：1111'), paragraph('https://pan.quark.cn/s/two'),
            paragraph('兰香如故 (2026)【47集全】'), RULE,
            paragraph('雀骨【更28集】'), paragraph('https://pan.quark.cn/s/outside'),
            RULE, paragraph('2026年11月4日热门更新'), RULE, paragraph('前一天【更3集】'), RULE,
        )
        section = wps.parse_hot_updates(content)
        self.assertEqual(section['date'], '2026-11-05')
        self.assertEqual(section['entries'], [
            {'title': '折月亮 (2026)【更4集】', 'links': [
                {'drive': 'baidu', 'url': 'https://pan.baidu.com/s/one?pwd=1111'},
                {'drive': 'quark', 'url': 'https://pan.quark.cn/s/two'},
            ]},
            {'title': '兰香如故 (2026)【47集全】', 'links': []},
        ])

    def test_rejects_missing_boundary(self):
        for blocks in [
            (paragraph('说明'), RULE),
            (HEADLINE, paragraph('剧名【更4集】'), RULE, RULE),
            (HEADLINE, RULE, paragraph('剧名【更4集】')),
        ]:
            with self.subTest(blocks=blocks), self.assertRaisesRegex(ValueError, '未找到完整的热门更新区块'):
                wps.parse_hot_updates(document(*blocks))
