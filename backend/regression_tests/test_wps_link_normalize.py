"""WPS 文档链接归一化：粘连的说明文字不能变成分享链接的一部分，同一分享只留一条。"""
import unittest

from app.application.wps_document_search import _share_link, parse_document


def paragraph(text, href=None):
    node = {'type': 'text', 'text': text}
    if href:
        node['marks'] = [{'type': 'link', 'attrs': {'href': href}}]
    return {'type': 'paragraph', 'content': [node]}


class ShareLinkNormalizeTests(unittest.TestCase):
    def test_trailing_chinese_note_is_not_part_of_share_url(self):
        link = _share_link('https://pan.quark.cn/s/f74735be7943夸克和谐太快建议使用百度云')
        self.assertEqual(link, {'drive': 'quark', 'url': 'https://pan.quark.cn/s/f74735be7943'})

    def test_passcode_query_is_kept_and_other_params_are_dropped(self):
        self.assertEqual(
            _share_link('https://pan.baidu.com/s/1FDxqzujCQy_sZIss3NS3Qw?pwd=1111&entry=share#/x')['url'],
            'https://pan.baidu.com/s/1FDxqzujCQy_sZIss3NS3Qw?pwd=1111',
        )
        self.assertEqual(
            _share_link('https://pan.xunlei.com/s/VP45Dv5l7MrdAm49BIORfLKsA1?pwd=cnbm')['url'],
            'https://pan.xunlei.com/s/VP45Dv5l7MrdAm49BIORfLKsA1?pwd=cnbm',
        )

    def test_trailing_punctuation_and_non_share_paths_are_rejected(self):
        self.assertEqual(_share_link('https://pan.quark.cn/s/abc1234。')['url'], 'https://pan.quark.cn/s/abc1234')
        for raw in ('https://pan.quark.cn/s/', 'https://pan.quark.cn/abc', 'https://example.com/s/abc'):
            self.assertIsNone(_share_link(raw), raw)

    def test_duplicate_variants_collapse_into_one_row(self):
        data = {'type': 'doc', 'content': [
            paragraph('国剧「嫁金钗 更新30全集」超前完结'),
            paragraph('夸克链接:https://pan.quark.cn/s/f74735be7943夸克和谐太快建议使用百度云',
                      'https://pan.quark.cn/s/f74735be7943'),
        ]}
        rows = parse_document(data)
        self.assertEqual([row['shareurl'] for row in rows], ['https://pan.quark.cn/s/f74735be7943'])


if __name__ == '__main__':
    unittest.main()
