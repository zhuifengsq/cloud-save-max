"""Example only: copy this file to the parent directory and replace the search logic."""

from app.extensions.search_plugins.base import BaseSearchPlugin


class SampleSearchPlugin(BaseSearchPlugin):
    key = "sample"
    name = "示例搜索插件"
    description = "请替换示例实现后再启用"

    def search(self, keyword: str) -> list[dict]:
        # Query your source with a finite timeout and return real share links.
        return []
