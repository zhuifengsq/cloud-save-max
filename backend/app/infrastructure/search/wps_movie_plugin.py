from app.infrastructure.search.wps_plugin import WpsSearchPlugin


class WpsMovieSearchPlugin(WpsSearchPlugin):
    key = 'wps_movie'
    name = 'wps电影'

    def configure(self, config):
        super().configure({'document_url': 'https://kdocs.cn/l/ctcFh0Mafjjq', **config})
