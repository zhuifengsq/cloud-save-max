"""Legacy shim: new path app.application.tasks (S4-6, 2026-11-09). Remove one release later."""
from app.application.tasks import (  # noqa: F401
    logger,
    list_tasks,
    list_tasks_recent_executions,
    get_task,
    list_recent_executions,
    create_task,
    update_task,
    set_task_enabled,
    delete_task,
)
