"""兼容垫片（S4-6 物理搬目录后保留一个版本）。

新路径：app.infrastructure.scheduling.task_admission
本文件不再放实现，只做 re-export；新代码请直接 import 新路径。
删除时机：S4-6 完成后一个版本（届时全仓库已无旧路径引用）。
"""
from app.infrastructure.scheduling.task_admission import (  # noqa: F401
    TaskAdmission,
    TaskSlot,
    task_admission,
)
