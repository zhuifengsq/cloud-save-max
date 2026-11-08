"""旧路径兼容包：适配器已迁至 app.infrastructure.drive.adapters（S4-6，2026-11-09）。

删除时机：一个版本之后。新代码请直接 import 新路径。
"""

from app.infrastructure.drive.adapters import *  # noqa: F401,F403
from app.infrastructure.drive.adapters import __all__ as __all__  # noqa: F401
