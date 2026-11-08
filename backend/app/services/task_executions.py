"""Legacy shim: new path app.application.task_executions (S4-6, 2026-11-09). Remove one release later."""
from app.application.task_executions import (  # noqa: F401
    logger,
    DEFAULT_HISTORY_LIMIT,
    history_limit,
    prune_task_executions,
    prune_all_task_executions,
)
