"""兼容垫片（S4-6 物理搬目录后保留一个版本）。

新路径：app.infrastructure.scheduling.task_scheduler
本文件不再放实现，只做 re-export；新代码请直接 import 新路径。
删除时机：S4-6 完成后一个版本（届时全仓库已无旧路径引用）。
"""
from app.infrastructure.scheduling.task_scheduler import (  # noqa: F401
    TaskSchedulerManager,
    run_drama_tasks,
    run_tmdb_cache_refresh,
    run_drive_account_probe,
    run_audit_log_cleanup,
    task_scheduler_manager,
)
