from __future__ import annotations

from app.application.dto.task_run import RunTaskCommand
from app.application.container import run_task_use_case

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.application.use_cases.run_task import RunPolicy
from app.application.container import adapter_factory as _acc_AdapterFactory; AdapterFactory = _acc_AdapterFactory()
from app.application.container import account_manager_cls as _acc_DatabaseAccountManager; DatabaseAccountManager = _acc_DatabaseAccountManager()
from app.application.container import adapter_registry as _acc_AdapterRegistry; AdapterRegistry = _acc_AdapterRegistry()
from app.application.container import task_executions_repo, tasks_repo
from app.application.dashboard_drama import build_drama_overview
from app.application.drive_accounts import (
    build_capacity_overview,
)
from app.application.tasks import (
    create_task,
    delete_task,
    get_task,
    list_tasks_recent_executions,
    set_task_enabled,
    update_task,
)


from app.application.telegram_bot.action_helpers import _dump_dt, _task_execution_summary, _task_to_dict

@dataclass
class TelegramTaskActions:
    db: Session

    def build_status_summary(self) -> dict[str, Any]:
        drama = build_drama_overview(self.db, days=30)
        capacity = build_capacity_overview(self.db)
        task_total = tasks_repo().count_tasks(self.db)
        task_enabled = tasks_repo().count_enabled_tasks(self.db)
        return {
            "tasks_total": int(task_total or 0),
            "tasks_enabled": int(task_enabled or 0),
            "drama_summary": drama.get("summary") or {},
            "capacity_summary": (capacity.get("summary") or {}),
            "updated_at": _dump_dt(datetime.now()),
        }


    def list_tasks(self, *, page: int = 1, page_size: int = 8, task_type: str | None = None) -> dict[str, Any]:
        page = max(1, int(page or 1))
        items = list_tasks_recent_executions(self.db, limit=3)
        if task_type:
            items = [x for x in items if str(getattr(x, "task_type", "") or "") == task_type]
        total = len(items)
        start = (page - 1) * page_size
        sliced = items[start : start + page_size]
        return {"items": [_task_to_dict(self.db, x) for x in sliced], "total": total, "page": page, "page_size": page_size}


    def get_task_detail(self, task_id: int) -> dict[str, Any]:
        return _task_to_dict(self.db, get_task(self.db, int(task_id)))


    def toggle_task(self, task_id: int) -> dict[str, Any]:
        task = get_task(self.db, int(task_id))
        task = set_task_enabled(self.db, int(task.id), not bool(task.enabled))
        self.db.commit()
        self.db.refresh(task)
        return _task_to_dict(self.db, task)


    def delete_task(self, task_id: int) -> None:
        delete_task(self.db, int(task_id))
        self.db.commit()


    def save_task(self, *, task_id: int | None = None, payload: dict[str, Any]) -> dict[str, Any]:
        data = dict(payload or {})
        if task_id is None:
            task = create_task(self.db, **data)
        else:
            task = update_task(self.db, int(task_id), **data)
        self.db.commit()
        self.db.refresh(task)
        return _task_to_dict(self.db, task)


    def run_task(self, task_id: int) -> dict[str, Any]:
        execution = run_task_use_case(self.db).execute(
            RunTaskCommand(int(task_id)), RunPolicy.for_trigger('telegram'))
        return _task_execution_summary(execution) or {}


    def list_task_executions(self, task_id: int, *, limit: int = 5) -> list[dict[str, Any]]:
        rows = task_executions_repo().list_desc(self.db, int(task_id), limit=int(limit))
        return [_task_execution_summary(x) or {} for x in rows]


    def replace_task_shareurl(
        self,
        task_id: int,
        shareurl: str,
        *,
        account_name: str | None = None,
        tmdb_id: int | None = None,
        tmdb_media_type: str | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"shareurl": shareurl}
        if account_name is not None:
            payload["account_name"] = account_name
        if tmdb_id:
            payload["tmdb_id"] = int(tmdb_id)
        if tmdb_media_type:
            payload["tmdb_media_type"] = str(tmdb_media_type)
        task = update_task(self.db, int(task_id), **payload)
        self.db.commit()
        self.db.refresh(task)
        return _task_to_dict(self.db, task)

