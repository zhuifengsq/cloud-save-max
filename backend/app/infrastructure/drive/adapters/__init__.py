# -*- coding: utf-8 -*-
"""
云盘适配器模块
支持多网盘的统一接口
"""

from app.infrastructure.drive.adapters.base_adapter import BaseCloudDriveAdapter
from app.infrastructure.drive.adapters.quark_adapter import QuarkAdapter
from app.infrastructure.drive.adapters.cloud115_adapter import Cloud115Adapter
from app.infrastructure.drive.adapters.baidu_adapter import BaiduAdapter
from app.infrastructure.drive.adapters.xunlei_adapter import XunleiAdapter
from app.infrastructure.drive.adapters.aliyun_adapter import AliyunAdapter
from app.infrastructure.drive.adapters.uc_adapter import UCAdapter
from app.infrastructure.drive.adapters.pan123_adapter import Pan123Adapter
from app.infrastructure.drive.adapters.cloud189_adapter import Cloud189Adapter
from app.infrastructure.drive.adapters.cloud139_adapter import Cloud139Adapter

__all__ = [
    "BaseCloudDriveAdapter",
    "QuarkAdapter",
    "Cloud115Adapter",
    "BaiduAdapter",
    "XunleiAdapter",
    "AliyunAdapter",
    "UCAdapter",
    "Pan123Adapter",
    "Cloud189Adapter",
    "Cloud139Adapter",
    "AdapterFactory",
    "AccountManager",
]

__version__ = "1.1.0"


def __getattr__(name: str):
    """PEP 562 惰性导出：factory 依赖本包，顶层直接 import 会成环。"""
    if name in ("AdapterFactory", "AccountManager"):
        from app.infrastructure.drive.factory import AdapterFactory, AccountManager

        return {"AdapterFactory": AdapterFactory, "AccountManager": AccountManager}[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
