from sqlalchemy.orm import Session

from app.application.container import system_settings_repo, tasks_repo
from app.core.errors import not_found


def drama_group_key(task_uid: str, tmdb_id: int | None, media_type: str | None) -> str:
    if tmdb_id:
        return f"tmdb:{media_type or 'unknown'}:{tmdb_id}"
    return f"task:{task_uid}"


def set_drama_pin(db: Session, task_id: int, pinned: bool) -> None:
    task = tasks_repo().get_task_row(db, task_id)
    if task is None or task.task_type != "drama":
        raise not_found("drama_task_not_found", "追剧任务不存在")
    group = drama_group_key(task.task_uid, task.tmdb_id, task.tmdb_media_type)
    system_settings_repo().set_drama_pin(db, group, pinned)
