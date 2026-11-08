"""Legacy shim: new path app.application.task_scheduler (S4-6, 2026-11-09). Remove one release later."""
from app.application.task_scheduler import (  # noqa: F401
    DEFAULT_TASK_SCHEDULER_SETTING_ID,
    load_task_scheduler_setting,
    get_or_create_task_scheduler_setting,
    update_task_scheduler_setting,
)
