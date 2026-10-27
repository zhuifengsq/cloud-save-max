# Python 搜索插件

管理员可在“搜索插件”页面上传 `<key>_plugin.py`，也可以将文件放到本目录后刷新列表。
插件必须定义一个继承 `BaseSearchPlugin` 的类，`key` 与文件名的 `<key>` 一致，
`search(keyword)` 返回字典列表，包含标题 `taskname`（或 `title`）及网盘分享链接
`shareurl`（或 `url`）。可选字段为 `datetime` 和 `content`。

见 `examples/sample_plugin.py`。上传的 Python 文件会在服务器上运行，因此只有
管理员可以上传、启停和测试插件。插件中的网络请求应设置超时。

内置的 `quark4k_plugin.py` 已适配自 pan-relay 的 Quark4K 搜索逻辑。它会查询
Quark4K 公开讨论的前两页，并提取当前项目支持的网盘分享链接。新插件默认关闭；
在“搜索源管理 → Python 搜索插件”中启用后即可参与追剧任务搜索。
