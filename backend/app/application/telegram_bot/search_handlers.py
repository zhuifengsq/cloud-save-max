from __future__ import annotations

from typing import Any
from sqlalchemy.orm import Session
from app.application.telegram_bot.formatters import button, keyboard, search_loading_message, search_results_keyboard, search_results_message, tmdb_results_keyboard, tmdb_results_message
from app.application.telegram_bot.session_store import load_session_data, save_session_data
from app.application.telegram_bot.context import BotContext
from app.application.telegram_bot.editor_helpers import (TASK_FIELDS, _flatten_seed, _get_nested, _preferred_task_id_from_session, _set_nested, _short_link_text, _tmdb_binding_label)


class SearchHandlers:
    def __init__(self, context: BotContext):
        self.context = context

    def show_search_prompt(self, db: Session, chat_id: int, *, replace_task_id: int | None = None, message_id: int | None = None) -> None:
        current = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        current_ctx = dict(current.context or {})
        text = "请输入搜索关键字。" if replace_task_id is None else f"请输入要为任务 #{replace_task_id} 搜索的新资源关键字。"
        if int(current_ctx.get("tmdb_id") or 0) > 0 and str(current_ctx.get("tmdb_media_type") or "").strip():
            text += f"\n\n当前已绑定 TMDB: {current_ctx.get('tmdb_media_type')} #{current_ctx.get('tmdb_id')}，输入后会优先按该 TMDB 标准标题搜索资源。"
        mid = self.context.transport.edit_or_send(
            chat_id,
            text,
            message_id=message_id,
            reply_markup=keyboard([[button("⬅️ 返回", "back"), button("✖️ 取消", "cancel")], [button("🏠 首页", "home")]]),
        )
        save_session_data(
            db,
            chat_id=chat_id,
            user_id=self.context.config.user_id,
            scene="search",
            step="await_keyword",
            context={
                "replace_task_id": replace_task_id or 0,
                "page": int(current_ctx.get("page") or 1),
                "task_type": str(current_ctx.get("task_type") or ""),
                "tmdb_id": int(current_ctx.get("tmdb_id") or 0),
                "tmdb_media_type": str(current_ctx.get("tmdb_media_type") or ""),
                "tmdb_title": str(current_ctx.get("tmdb_title") or ""),
            },
            last_message_id=mid,
        )
        db.commit()

    def show_search_results(self, db: Session, chat_id: int, *, keyword: str, results: list[dict[str, Any]], page: int = 1, replace_task_id: int | None = None, message_id: int | None = None) -> None:
        current = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        current_ctx = dict(current.context or {})
        selected_tmdb = dict(current_ctx.get("selected_tmdb") or {})
        payload = {"items": results, "selected_tmdb": selected_tmdb}
        mid = self.context.transport.edit_or_send(
            chat_id,
            search_results_message(keyword, payload, page=page, page_size=6, replace_mode=bool(replace_task_id)),
            message_id=message_id,
            reply_markup=search_results_keyboard(len(results), page=page, page_size=6, replace_mode=bool(replace_task_id)),
        )
        save_session_data(
            db,
            chat_id=chat_id,
            user_id=self.context.config.user_id,
            scene="search_results",
            step="idle",
            context={
                "keyword": keyword,
                "results": results,
                "replace_task_id": replace_task_id or 0,
                "page": page,
                "selected_tmdb": selected_tmdb,
                "task_type": str(current_ctx.get("task_type") or ""),
            },
            last_message_id=mid,
        )
        db.commit()

    def show_task_tmdb_prompt(self, db: Session, chat_id: int, *, message_id: int | None = None) -> None:
        session = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        ctx = dict(session.context or {})
        draft = dict(ctx.get("draft") or {})
        lines = [
            "请输入 TMDB 关键词。",
            f"当前绑定: {_tmdb_binding_label(draft)}",
            "会先搜索 TMDB，选择条目后自动回填到任务草稿。",
        ]
        rows = [[button("✏️ 返回编辑器", "tm", "back"), button("🏠 首页", "home")]]
        if int(_get_nested(draft, "tmdb_id") or 0) > 0 and str(_get_nested(draft, "tmdb_media_type") or "").strip():
            rows.insert(0, [button("🧹 清空绑定", "tm", "clear")])
        mid = self.context.transport.edit_or_send(chat_id, "\n".join(lines), message_id=message_id or session.last_message_id, reply_markup=keyboard(rows))
        save_session_data(
            db,
            chat_id=chat_id,
            user_id=self.context.config.user_id,
            scene="task_tmdb_input",
            step="await_task_tmdb_keyword",
            context=ctx,
            last_message_id=mid,
        )
        db.commit()

    def show_tmdb_results(
        self,
        db: Session,
        chat_id: int,
        *,
        keyword: str,
        items: list[dict[str, Any]],
        page: int = 1,
        replace_task_id: int | None = None,
        message_id: int | None = None,
        message: str | None = None,
        bind_mode: bool = False,
    ) -> None:
        current = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        current_ctx = dict(current.context or {})
        payload = {"items": items, "message": message}
        mid = self.context.transport.edit_or_send(
            chat_id,
            tmdb_results_message(keyword, payload, page=page, page_size=5, replace_mode=bool(replace_task_id), bind_mode=bind_mode),
            message_id=message_id,
            reply_markup=tmdb_results_keyboard(
                len(items),
                page=page,
                page_size=5,
                bind_mode=bind_mode,
                has_binding=bool(int(current_ctx.get("tmdb_id") or 0) > 0 and str(current_ctx.get("tmdb_media_type") or "").strip()),
            ),
        )
        save_session_data(
            db,
            chat_id=chat_id,
            user_id=self.context.config.user_id,
            scene="tmdb_results",
            step="idle",
            context={
                **current_ctx,
                "keyword": keyword,
                "tmdb_results": items,
                "replace_task_id": replace_task_id or 0,
                "page": int(current_ctx.get("page") or 1),
                "task_type": str(current_ctx.get("task_type") or ""),
                "tmdb_bind_mode": bool(bind_mode),
            },
            last_message_id=mid,
        )
        db.commit()

    def run_search_resources(
        self,
        db: Session,
        chat_id: int,
        *,
        input_keyword: str,
        resource_keyword: str,
        replace_task_id: int | None,
        message_id: int | None,
        selected_tmdb: dict[str, Any] | None = None,
    ) -> None:
        loading_mid = self.context.transport.edit_or_send(
            chat_id,
            search_loading_message(resource_keyword, replace_mode=bool(replace_task_id)),
            message_id=message_id,
            reply_markup=keyboard([[button("✖️ 取消", "cancel"), button("🏠 首页", "home")]]),
        )
        ctx = dict(load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id).context or {})
        ctx["selected_tmdb"] = dict(selected_tmdb or {})
        save_session_data(
            db,
            chat_id=chat_id,
            user_id=self.context.config.user_id,
            scene="search",
            step="searching",
            context=ctx,
            last_message_id=loading_mid or message_id,
        )
        db.commit()
        result = self.context.transport.actions(db).search_resources(resource_keyword)
        save_session_data(
            db,
            chat_id=chat_id,
            user_id=self.context.config.user_id,
            context={**ctx, "selected_tmdb": dict(selected_tmdb or {})},
            last_message_id=loading_mid or message_id,
        )
        db.commit()
        self.show_search_results(
            db,
            chat_id,
            keyword=input_keyword,
            results=list(result.get("items") or []),
            page=1,
            replace_task_id=replace_task_id,
            message_id=loading_mid or message_id,
        )

    def show_share_candidate_picker(
        self,
        db: Session,
        chat_id: int,
        *,
        text: str,
        candidates: list[dict[str, Any]],
        session,
        message_id: int | None = None,
    ) -> None:
        rows: list[list[dict[str, str]]] = []
        lines = [
            "🔗 检测到多条网盘链接",
            "",
            "请选择要用于创建或修改任务的链接：",
        ]
        for idx, item in enumerate(candidates):
            shareurl = str(item.get("shareurl") or "").strip()
            drive_type = str(item.get("drive_type") or "-").strip() or "-"
            lines.append(f"#{idx + 1} {drive_type} · {_short_link_text(shareurl, 72)}")
            rows.append([button(f"#{idx + 1} {drive_type} · {_short_link_text(shareurl, 28)}", "shr", "pick", idx)])
        rows.append([button("✖️ 取消", "cancel"), button("🏠 首页", "home")])
        mid = self.context.transport.edit_or_send(chat_id, "\n".join(lines), message_id=message_id or session.last_message_id, reply_markup=keyboard(rows))
        ctx = dict(session.context or {})
        ctx["share_text"] = text
        ctx["share_candidates"] = candidates
        save_session_data(
            db,
            chat_id=chat_id,
            user_id=self.context.config.user_id,
            scene="share_picker",
            step="idle",
            context=ctx,
            last_message_id=mid,
        )
        db.commit()

    def apply_share_text(self, db: Session, chat_id: int, text: str, session, *, selected_shareurl: str | None = None) -> bool:
        shareurl_hint = str(selected_shareurl or "").strip()
        candidates = self.context.transport.actions(db).extract_share_candidates(text)
        if not candidates:
            return False
        if not shareurl_hint and len(candidates) > 1:
            self.show_share_candidate_picker(db, chat_id, text=text, candidates=candidates, session=session)
            return True
        picked_shareurl = shareurl_hint or str(candidates[0].get("shareurl") or "")
        loading_mid = self.context.transport.edit_or_send(
            chat_id,
            "⏳ 正在解析分享链接并准备任务草稿...\n\n识别到消息中包含网盘链接，正在自动进入任务流程。",
            message_id=session.last_message_id,
            reply_markup=keyboard([[button("✖️ 取消", "cancel"), button("🏠 首页", "home")]]),
        )
        info = self.context.transport.actions(db).inspect_share_candidate(text, picked_shareurl)
        if not info or not bool(info.get("parsed")):
            self.context.transport.edit_or_send(
                chat_id,
                "未能解析这条网盘分享链接，请检查链接或提取码后重试。",
                message_id=loading_mid or session.last_message_id,
                reply_markup=keyboard([[button("🏠 首页", "home")]]),
            )
            return True
        ctx = dict(session.context or {})
        scene = str(getattr(session, "scene", "") or "")
        preferred_task_id = _preferred_task_id_from_session(scene, ctx)
        matched_task = dict(info.get("matched_task") or {})
        target_task_id = preferred_task_id or (int(matched_task.get("id") or 0) or None)
        shareurl = str(info.get("resolved_shareurl") or info.get("shareurl") or text).strip()
        preview_message = str(info.get("preview_message") or "").strip()

        if target_task_id:
            item = self.context.transport.actions(db).get_task_detail(target_task_id)
            current_shareurl = str(item.get("shareurl") or "").strip()
            if shareurl and shareurl != current_shareurl:
                item["shareurl"] = shareurl
            item, preview_message = self.context.editor.apply_task_share_autofill(
                db,
                item,
                raw_text=text,
                selected_shareurl=picked_shareurl,
            )
            title = f"基于分享链接修改任务 #{target_task_id}"
            if preview_message and not bool(info.get("preview_ok")):
                title += f"\n⚠️ 预检: {preview_message}"
            self.context.editor.start_editor(
                db,
                chat_id,
                kind="task",
                title=title,
                fields=TASK_FIELDS,
                draft=_flatten_seed(TASK_FIELDS, item),
                target_id=target_task_id,
                message_id=loading_mid or session.last_message_id,
                extras={"page": int(ctx.get("page") or 1), "task_type": str(ctx.get("task_type") or item.get("task_type") or "")},
            )
            return True

        seed: dict[str, Any] = {
            "task_type": "drama",
            "shareurl": shareurl,
            "enabled": True,
        }
        seed, preview_message = self.context.editor.apply_task_share_autofill(
            db,
            seed,
            raw_text=text,
            selected_shareurl=picked_shareurl,
        )
        title = "基于分享链接新建任务"
        if preview_message and not bool(info.get("preview_ok")):
            title += f"\n⚠️ 预检: {preview_message}"
        self.context.editor.start_editor(
            db,
            chat_id,
            kind="task",
            title=title,
            fields=TASK_FIELDS,
            draft=_flatten_seed(TASK_FIELDS, seed),
            target_id=None,
            message_id=loading_mid or session.last_message_id,
            extras={"page": int(ctx.get("page") or 1), "task_type": str(ctx.get("task_type") or "")},
        )
        return True

    def handle_share_text(self, db: Session, chat_id: int, text: str, session) -> bool:
        return self.apply_share_text(db, chat_id, text, session)

    def dispatch_tmdb_results(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        session = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        ctx = dict(session.context or {})
        items = list(ctx.get("tmdb_results") or [])
        keyword = str(ctx.get("keyword") or "")
        replace_task_id = int(ctx.get("replace_task_id") or 0) or None
        action = parts[0] if parts else "list"
        bind_mode = bool(ctx.get("tmdb_bind_mode"))
        if action == "list":
            page = int(parts[1]) if len(parts) > 1 and str(parts[1]).isdigit() else 1
            self.show_tmdb_results(db, chat_id, keyword=keyword, items=items, page=page, replace_task_id=replace_task_id, message_id=message_id, bind_mode=bind_mode)
            return
        if action == "pick":
            idx = int(parts[1]) if len(parts) > 1 else -1
            if idx < 0 or idx >= len(items):
                raise ValueError("TMDB 条目不存在")
            item = dict(items[idx] or {})
            if bind_mode:
                draft = dict(ctx.get("draft") or {})
                _set_nested(draft, "tmdb_id", int(item.get("id") or 0) or None)
                _set_nested(draft, "tmdb_media_type", str(item.get("media_type") or ""))
                _set_nested(draft, "__tmdb_title__", str(item.get("display_title") or item.get("title") or item.get("name") or "").strip())
                ctx["draft"] = draft
                ctx.pop("tmdb_results", None)
                ctx.pop("keyword", None)
                ctx.pop("tmdb_bind_mode", None)
                save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="task_editor", step="idle", context=ctx, last_message_id=message_id)
                db.commit()
                self.context.editor.resume_task_editor(db, chat_id, ctx=ctx, message_id=message_id)
                return
            resource_keyword = str(item.get("standard_keyword") or item.get("display_title") or keyword).strip() or keyword
            self.run_search_resources(
                db,
                chat_id,
                input_keyword=keyword,
                resource_keyword=resource_keyword,
                replace_task_id=replace_task_id,
                message_id=message_id,
                selected_tmdb=item,
            )
            return
        if action == "input" and bind_mode:
            self.show_task_tmdb_prompt(db, chat_id, message_id=message_id)
            return
        if action == "back" and bind_mode:
            ctx.pop("tmdb_results", None)
            ctx.pop("keyword", None)
            ctx.pop("tmdb_bind_mode", None)
            save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="task_editor", step="idle", context=ctx, last_message_id=message_id)
            db.commit()
            self.context.editor.resume_task_editor(db, chat_id, ctx=ctx, message_id=message_id)
            return
        if action == "clear" and bind_mode:
            draft = dict(ctx.get("draft") or {})
            _set_nested(draft, "tmdb_id", None)
            _set_nested(draft, "tmdb_media_type", "")
            _set_nested(draft, "__tmdb_title__", "")
            ctx["draft"] = draft
            ctx.pop("tmdb_results", None)
            ctx.pop("keyword", None)
            ctx.pop("tmdb_bind_mode", None)
            save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="task_editor", step="idle", context=ctx, last_message_id=message_id)
            db.commit()
            self.context.editor.resume_task_editor(db, chat_id, ctx=ctx, message_id=message_id)
            return
        if action == "skip":
            self.run_search_resources(
                db,
                chat_id,
                input_keyword=keyword,
                resource_keyword=keyword,
                replace_task_id=replace_task_id,
                message_id=message_id,
                selected_tmdb=None,
            )
            return
        self.show_tmdb_results(db, chat_id, keyword=keyword, items=items, page=1, replace_task_id=replace_task_id, message_id=message_id)

    def dispatch_search_results(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        session = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        ctx = session.context or {}
        results = list(ctx.get("results") or [])
        keyword = str(ctx.get("keyword") or "")
        replace_task_id = int(ctx.get("replace_task_id") or 0) or None
        selected_tmdb = dict(ctx.get("selected_tmdb") or {})
        action = parts[0] if parts else "list"
        if action == "list":
            page = int(parts[1]) if len(parts) > 1 and str(parts[1]).isdigit() else 1
            self.show_search_results(db, chat_id, keyword=keyword, results=results, page=page, replace_task_id=replace_task_id, message_id=message_id)
        elif action == "create":
            idx = int(parts[1])
            if idx >= len(results):
                raise ValueError("搜索结果不存在")
            item = results[idx]
            seed = {"task_type": "drama", "taskname": item.get("taskname") or "", "shareurl": item.get("shareurl") or "", "enabled": True}
            if int(selected_tmdb.get("id") or 0) > 0 and str(selected_tmdb.get("media_type") or "").strip():
                seed["tmdb_id"] = int(selected_tmdb.get("id") or 0)
                seed["tmdb_media_type"] = str(selected_tmdb.get("media_type") or "")
                seed["__tmdb_title__"] = str(selected_tmdb.get("display_title") or selected_tmdb.get("title") or selected_tmdb.get("name") or "")
                seed["taskname"] = str(selected_tmdb.get("display_title") or selected_tmdb.get("title") or selected_tmdb.get("name") or seed["taskname"])
            draft = _flatten_seed(TASK_FIELDS, seed)
            self.context.editor.start_editor(db, chat_id, kind="task", title="基于搜索结果新建任务", fields=TASK_FIELDS, draft=draft, message_id=message_id)
        elif action == "replace":
            idx = int(parts[1])
            if idx >= len(results):
                raise ValueError("搜索结果不存在")
            if not replace_task_id:
                raise ValueError("没有指定要替换的任务")
            item = results[idx]
            task = self.context.transport.actions(db).replace_task_shareurl(
                replace_task_id,
                str(item.get("shareurl") or ""),
                tmdb_id=int(selected_tmdb.get("id") or 0) or None,
                tmdb_media_type=str(selected_tmdb.get("media_type") or "") or None,
            )
            self.context.task_handlers.show_task_detail(
                db,
                chat_id,
                int(task["id"]),
                message_id=message_id,
                back_page=int(ctx.get("page") or 1),
                task_type=str(ctx.get("task_type") or "") or None,
            )

    def dispatch_share_picker(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        session = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        ctx = dict(session.context or {})
        candidates = list(ctx.get("share_candidates") or [])
        text = str(ctx.get("share_text") or "")
        action = parts[0] if parts else "list"
        if action == "pick":
            idx = int(parts[1]) if len(parts) > 1 else -1
            if idx < 0 or idx >= len(candidates):
                raise ValueError("分享链接不存在")
            shareurl = str((candidates[idx] or {}).get("shareurl") or "").strip()
            if not shareurl:
                raise ValueError("分享链接不存在")
            self.apply_share_text(db, chat_id, text, session, selected_shareurl=shareurl)
            return
        self.context.transport.show_home(db, chat_id, message_id=message_id)
