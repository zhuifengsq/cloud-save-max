from app.infrastructure.search.base import BaseSearchPlugin
from app.application import wps_document_search as wps


class WpsSearchPlugin(BaseSearchPlugin):
    key = 'wps'
    name = 'WPS 文档搜索'
    description = '从公开 WPS 文档提取剧名和网盘链接，无需登录；文档缓存 15 分钟。'
    timeout_seconds = 28
    configurable = True
    supports_cache = True

    def configure(self, config):
        self.config = wps.validate_config(config)

    def search(self, keyword):
        return wps.search(keyword, self.config)

    def clear_cache(self):
        wps.clear_cache(self.config['document_url'])

    def cache_status(self):
        return wps.cache_status(self.config['document_url'])
