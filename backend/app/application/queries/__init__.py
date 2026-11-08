"""读模型查询（application/queries）。

约定（与 docs/architecture-evolution-plan.md §4.3 一致）：
- 路由只依赖这里返回的视图对象（frozen dataclass），不再自己编排「查什么、怎么拼」；
- 视图不含 ORM 对象、不含 Session，只有值；响应模型由 api/presenters 负责；
- 批量关系（TMDB 状态/payload、追剧引流摘要）必须在本层一次查完，禁止 N+1。
"""

from app.application.queries.audit_logs import AuditLogView, list_audit_log_views  # noqa: F401
from app.application.queries.task_views import (  # noqa: F401
    TaskExecutionView,
    TaskSchedulerSettingView,
    TaskView,
    execution_view,
    list_recent_execution_views,
    list_task_views,
    scheduler_view,
    task_detail_view,
    task_view,
)
