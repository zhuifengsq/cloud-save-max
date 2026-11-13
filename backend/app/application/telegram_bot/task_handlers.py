from __future__ import annotations

from sqlalchemy.orm import Session
from app.core.errors import ApiError
from app.application.container import db_session
from app.application.telegram_bot.formatters import button, keyboard, pagination_row, task_detail_keyboard, task_detail_message, tasks_message
from app.application.telegram_bot.session_store import load_session_data, save_session_data
from app.application.telegram_bot.context import BotContext
from app.application.telegram_bot.editor_helpers import (TASK_FIELDS, _editor_title, _flatten_seed)
from app.application.container import task_admission_service
task_admission = task_admission_service()


class TaskHandlers:
    def __init__(self, context: BotContext):
        self.context = context

    def show_tasks(self, db: Session, chat_id: int, *, page: int = 1, _task_type: str | None = None, message_id: int | None = None) -> None:
        effective_task_type = "drama"
        payload = self.context.transport.actions(db).list_tasks(page=page, task_type=effective_task_type)
        rows = [[button(f"#{item['id']} {item['taskname'][:20]}", "tsk", "detail", item["id"])] for item in payload["items"]]
        rows.append([button("➕ 新建任务", "tsk", "new"), button("🏠 首页", "home")])
        rows.append(pagination_row("tsk:list", payload["page"], payload["total"], payload["page_size"], effective_task_type))
        mid = self.context.transport.edit_or_send(chat_id, tasks_message(payload, title="追剧任务"), message_id=message_id, reply_markup=keyboard(rows))
        save_session_data(
            db,
            chat_id=chat_id,
            user_id=self.context.config.user_id,
            scene="tasks",
            step="idle",
            context={"page": page, "task_type": effective_task_type},
            last_message_id=mid,
        )
        db.commit()

    def show_task_detail(
        self,
        db: Session,
        chat_id: int,
        task_id: int,
        *,
        message_id: int | None = None,
        back_page: int = 1,
        task_type: str | None = None,
    ) -> None:
        item = self.context.transport.actions(db).get_task_detail(task_id)
        mid = self.context.transport.edit_or_send(
            chat_id,
            task_detail_message(item),
            message_id=message_id,
            reply_markup=task_detail_keyboard(task_id, bool(item.get("enabled")), back_page=back_page, task_type=task_type),
        )
        save_session_data(
            db,
            chat_id=chat_id,
            user_id=self.context.config.user_id,
            scene="task_detail",
            step="idle",
            context={"task_id": task_id, "page": back_page, "task_type": task_type or ""},
            last_message_id=mid,
        )
        db.commit()

    def dispatch_task(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        session = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        ctx = dict(session.context or {})
        action = parts[0] if parts else "list"
        if action == "list":
            page = 1
            task_type = None
            if len(parts) > 1:
                if str(parts[1]).isdigit():
                    page = int(parts[1])
                    if len(parts) > 2 and parts[2] not in {"all", ""}:
                        task_type = parts[2]
                else:
                    task_type = parts[1] if parts[1] not in {"all", ""} else None
                    if len(parts) > 2 and str(parts[2]).isdigit():
                        page = int(parts[2])
            task_type = "drama"
            self.show_tasks(db, chat_id, page=page, task_type=task_type, message_id=message_id)
        elif action == "detail":
            back_page = int(ctx.get("page") or 1)
            task_type = str(ctx.get("task_type") or "")
            self.show_task_detail(db, chat_id, int(parts[1]), message_id=message_id, back_page=back_page, task_type=task_type or None)
        elif action == "run":
            task_id = int(parts[1])
            self.run_async(chat_id, message_id, task_id, f"任务 #{task_id} 执行中...", lambda adb: self.context.transport.actions(adb).run_task(task_id), "任务执行完成")
        elif action == "toggle":
            item = self.context.transport.actions(db).toggle_task(int(parts[1]))
            self.show_task_detail(
                db,
                chat_id,
                int(item["id"]),
                message_id=message_id,
                back_page=int(ctx.get("page") or 1),
                task_type=str(ctx.get("task_type") or "") or None,
            )
        elif action == "edit":
            task_id = int(parts[1])
            item = self.context.transport.actions(db).get_task_detail(task_id)
            self.context.editor.start_editor(
                db,
                chat_id,
                kind="task",
                title=_editor_title("task", task_id),
                fields=TASK_FIELDS,
                draft=_flatten_seed(TASK_FIELDS, item),
                target_id=task_id,
                message_id=message_id,
                extras={"page": int(ctx.get("page") or 1), "task_type": str(ctx.get("task_type") or "")},
            )
        elif action == "new":
            self.context.editor.start_editor(
                db,
                chat_id,
                kind="task",
                title=_editor_title("task"),
                fields=TASK_FIELDS,
                draft=_flatten_seed(TASK_FIELDS),
                target_id=None,
                message_id=message_id,
                extras={"page": int(ctx.get("page") or 1), "task_type": str(ctx.get("task_type") or "")},
            )
        elif action == "field":
            target_id = None if parts[1] == "new" else int(parts[1])
            field = parts[2]
            self.context.editor.handle_editor_field_click(db, chat_id, message_id, kind="task", fields=TASK_FIELDS, target_id=target_id, field=field)
        elif action == "save":
            target_id = None if parts[1] == "new" else int(parts[1])
            self.context.editor.handle_editor_save(db, chat_id, message_id, kind="task", fields=TASK_FIELDS, target_id=target_id)
        elif action == "search":
            task_id = int(parts[1])
            item = self.context.transport.actions(db).get_task_detail(task_id)
            self.context.search_handlers.show_search_prompt(db, chat_id, replace_task_id=task_id, message_id=message_id)
            save_session_data(
                db,
                chat_id=chat_id,
                user_id=self.context.config.user_id,
                scene="search",
                step="await_keyword",
                context={
                    "replace_task_id": task_id,
                    "page": int(ctx.get("page") or 1),
                    "task_type": str(ctx.get("task_type") or ""),
                    "tmdb_id": int(item.get("tmdb_id") or 0),
                    "tmdb_media_type": str(item.get("tmdb_media_type") or ""),
                    "tmdb_title": str(item.get("taskname") or ""),
                },
                last_message_id=message_id,
            )
            db.commit()
        elif action == "execs":
            task_id = int(parts[1])
            rows = self.context.transport.actions(db).list_task_executions(task_id, limit=5)
            lines = [f"任务 #{task_id} 最近执行"]
            for item in rows:
                lines.append(f"#{item.get('id')} {item.get('status')} {item.get('message') or '-'}")
            self.context.transport.edit_or_send(chat_id, "\n".join(lines), message_id=message_id, reply_markup=keyboard([[button("📌 返回任务详情", "tsk", "detail", task_id)], [button("🏠 首页", "home")]]))
        elif action == "delete":
            task_id = int(parts[1])
            self.context.transport.edit_or_send(chat_id, f"⚠️ 确认删除任务 #{task_id} 吗？", message_id=message_id, reply_markup=keyboard([[button("🗑️ 确认删除", "confirm", "taskdel", task_id)], [button("✖️ 取消", "tsk", "detail", task_id)]]))

    def dispatch_confirm(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        session = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        ctx = dict(session.context or {})
        action = parts[0] if parts else ""
        target_id = int(parts[1]) if len(parts) > 1 else 0
        if action == "taskdel":
            self.context.transport.actions(db).delete_task(target_id)
            self.show_tasks(db, chat_id, page=int(ctx.get("page") or 1), task_type=str(ctx.get("task_type") or "") or None, message_id=message_id)

    def run_async(self, chat_id: int, message_id: int, task_id: int, pending_text: str, job, success_title: str) -> None:
        try:
            slot = task_admission.reserve(task_id)
        except ApiError as exc:
            self.context.transport.edit_or_send(chat_id, exc.message, message_id=message_id, reply_markup=keyboard([[button("🏠 首页", "home")]]))
            return
        try:
            self.context.transport.edit_or_send(chat_id, pending_text, message_id=message_id, reply_markup=keyboard([[button("🏠 首页", "home")]]))
        except BaseException:
            slot.release()
            raise

        def worker() -> None:
            with db_session() as db:
                try:
                    result = job(db)
                    lines = [success_title, str(result)]
                except Exception as exc:
                    lines = [f"{success_title}失败", str(exc)]
                self.context.transport.send(chat_id, "\n".join(lines), reply_markup=keyboard([[button("🏠 首页", "home")]]))

        slot.start_worker(worker)
