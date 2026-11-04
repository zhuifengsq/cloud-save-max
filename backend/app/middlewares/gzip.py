"""gzip 压缩中间件（只对文本类响应生效）。

直接复用 Starlette 的 GZipMiddleware，它默认只会跳过 text/event-stream。
这里把图片/音视频/压缩包等“本身已压缩、再压只会白烧 CPU”的类型也加入排除列表，
避免代理海报、文件下载走一遍 gzip（几百 KB 的图片压缩几乎不减小体积）。

注意：Starlette 的 responder 在响应开始时读取 starlette.middleware.gzip 模块级常量，
因此在模块导入时覆盖它即可生效；若未来升级 Starlette 改了这处实现，最坏情况只是
图片重新被压缩（行为退化），不会导致功能异常。
"""

from __future__ import annotations

from starlette.middleware import gzip as _starlette_gzip

EXCLUDED_CONTENT_TYPES = (
    "text/event-stream",
    "image/",
    "audio/",
    "video/",
    "font/",
    "application/zip",
    "application/gzip",
    "application/octet-stream",
)

_starlette_gzip.DEFAULT_EXCLUDED_CONTENT_TYPES = EXCLUDED_CONTENT_TYPES

GZipMiddleware = _starlette_gzip.GZipMiddleware

__all__ = ["GZipMiddleware", "EXCLUDED_CONTENT_TYPES"]

