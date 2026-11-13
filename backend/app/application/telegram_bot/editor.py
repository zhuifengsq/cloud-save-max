from __future__ import annotations

from typing import Any
from sqlalchemy.orm import Session
from app.application.telegram_bot.actions import _to_sort_ts
from app.application.telegram_bot.formatters import button, editor_keyboard, editor_message, file_size_text, keyboard, pagination_row
from app.application.telegram_bot.session_store import load_session_data, reset_session, save_session_data
from app.application.telegram_bot.context import BotContext
from app.application.telegram_bot.editor_helpers import (ACCOUNT_FIELDS, TASK_FIELDS, _collapse_payload, _display_datetime, _editor_fields, _editor_title, _extract_task_share_fid, _field_meta, _field_prompt, _get_nested, _is_video_preview_item, _magic_rule_binding_label, _magic_rule_brief, _parse_scalar, _remote_parent_path, _set_nested, _setting_field_meta, _share_folder_label, _short_link_text, _suggest_taskname_from_share_text, _task_shareurl, _tmdb_binding_label)


class EditorHandlers:
    def __init__(self, context: BotContext):
        self.context = context

    def resume_task_editor(self, db: Session, chat_id: int, *, ctx: dict[str, Any], message_id: int | None = None) -> None:
        draft = dict(ctx.get("draft") or {})
        self.start_editor(
            db,
            chat_id,
            kind="task",
            title=_editor_title("task", int(ctx.get("target_id") or 0) or None),
            fields=TASK_FIELDS,
            draft=draft,
            target_id=int(ctx.get("target_id") or 0) or None,
            message_id=message_id,
            extras={k: v for k, v in ctx.items() if k != "draft"},
        )

    def resume_editor(self, db: Session, chat_id: int, *, kind: str, ctx: dict[str, Any], message_id: int | None = None) -> None:
        draft = dict(ctx.get("draft") or {})
        fields = TASK_FIELDS if kind == "task" else ACCOUNT_FIELDS
        self.start_editor(
            db,
            chat_id,
            kind=kind,
            title=_editor_title(kind, int(ctx.get("target_id") or 0) or None),
            fields=fields,
            draft=draft,
            target_id=int(ctx.get("target_id") or 0) or None,
            message_id=message_id,
            extras={k: v for k, v in ctx.items() if k != "draft"},
        )

    def show_task_drive_browser(self, db: Session, chat_id: int, *, message_id: int | None = None) -> None:
        session = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        ctx = dict(session.context or {})
        draft = dict(ctx.get("draft") or {})
        browse_path = str(ctx.get("browse_path") or _get_nested(draft, "savepath") or "/")
        payload = self.context.transport.actions(db).browse_task_drive(
            dir_path=browse_path,
            account_name=_get_nested(draft, "account_name"),
            shareurl=_get_nested(draft, "shareurl"),
            max_items=20,
        )
        rows: list[list[dict[str, str]]] = []
        if payload.get("dir_path") not in {"", "/"}:
            rows.append([button("⬆️ 上一级", "brw", "task", "up")])
        rows.append([button("✅ 选择当前目录", "brw", "task", "sel"), button("🔄 刷新", "brw", "task", "refresh")])
        for idx, item in enumerate(payload.get("items") or []):
            prefix = "[DIR] " if item.get("is_dir") else "[FILE] "
            rows.append([button(f"{prefix}{str(item.get('name') or '')[:26]}", "brw", "task", "open", idx)])
        rows.append([button("✏️ 返回编辑器", "brw", "task", "back"), button("🏠 首页", "home")])
        text_lines = [
            "选择任务保存目录",
            f"账号: {payload.get('account_name') or '-'}",
            f"路径: {payload.get('dir_path') or '/'}",
        ]
        if not payload.get("exists"):
            text_lines.append("当前路径不存在，可先返回后手动输入。")
        mid = self.context.transport.edit_or_send(chat_id, "\n".join(text_lines), message_id=message_id or session.last_message_id, reply_markup=keyboard(rows))
        ctx["browse_path"] = payload.get("dir_path") or "/"
        save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="task_browser", step="idle", context=ctx, last_message_id=mid)
        db.commit()

    def show_task_account_selector(self, db: Session, chat_id: int, *, message_id: int | None = None) -> None:
        session = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        ctx = dict(session.context or {})
        draft = dict(ctx.get("draft") or {})
        current = str(_get_nested(draft, "account_name") or "").strip()
        options = self.context.transport.actions(db).list_account_names()
        rows: list[list[dict[str, str]]] = []
        auto_mark = "✓" if not current else "○"
        rows.append([button(f"{auto_mark} 自动", "asel", "pick", "auto")])
        for idx, name in enumerate(options):
            mark = "✓" if current == str(name) else "○"
            rows.append([button(f"{mark} {str(name)}", "asel", "pick", idx)])
        rows.append([button("✏️ 返回编辑器", "asel", "back"), button("🏠 首页", "home")])
        lines = [
            "选择执行账号",
            f"当前: {current or '自动'}",
            "自动模式会按分享链接类型匹配默认可用账号。",
        ]
        mid = self.context.transport.edit_or_send(chat_id, "\n".join(lines), message_id=message_id or session.last_message_id, reply_markup=keyboard(rows))
        ctx["account_selector_options"] = options
        save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="task_account_selector", step="idle", context=ctx, last_message_id=mid)
        db.commit()

    def show_task_magic_rule_selector(self, db: Session, chat_id: int, *, message_id: int | None = None) -> None:
        session = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        ctx = dict(session.context or {})
        draft = dict(ctx.get("draft") or {})
        page = max(1, int(ctx.get("magic_rule_page") or 1))
        rules = self.context.transport.actions(db).list_magic_regex_rules()
        current_key = str(_get_nested(draft, "__magic_rule_key__") or "").strip()
        page_size = 8
        total = len(rules)
        max_page = max(1, (total + page_size - 1) // page_size)
        page = min(page, max_page)
        start = (page - 1) * page_size
        items = rules[start : start + page_size]
        rows: list[list[dict[str, str]]] = []
        for idx, rule in enumerate(items, start=start):
            mark = "✓" if current_key and current_key == str(rule.get("key") or "") else "○"
            rows.append([button(f"{mark} {_magic_rule_brief(rule)[:28]}", "rsel", "pick", idx)])
        rows.append([button("🧹 清空规则", "rsel", "clear"), button("✏️ 返回编辑器", "rsel", "back")])
        rows.append(pagination_row("rsel:list", page, total, page_size))
        rows.append([button("🏠 首页", "home")])
        lines = [
            "选择内置规则",
            f"当前: {_magic_rule_binding_label(draft)}",
            f"页码: {page}/{max_page}",
            "选择后会把默认 pattern / replace 自动填入，之后仍可继续手动修改。",
        ]
        mid = self.context.transport.edit_or_send(chat_id, "\n".join(lines), message_id=message_id or session.last_message_id, reply_markup=keyboard(rows))
        ctx["magic_rule_options"] = rules
        ctx["magic_rule_page"] = page
        save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="task_magic_rule_selector", step="idle", context=ctx, last_message_id=mid)
        db.commit()

    def task_preview_kwargs(self, draft: dict[str, Any]) -> dict[str, Any]:
        tmdb_id_raw = _get_nested(draft, "tmdb_id")
        tmdb_id = None
        if tmdb_id_raw not in (None, "", 0):
            try:
                tmdb_id = int(tmdb_id_raw)
            except Exception:
                tmdb_id = None
        return {
            "taskname": str(_get_nested(draft, "taskname") or "").strip() or None,
            "pattern": str(_get_nested(draft, "pattern") or "").strip() or None,
            "replace": str(_get_nested(draft, "replace") or "").strip() or None,
            "savepath": str(_get_nested(draft, "savepath") or "").strip() or None,
            "ignore_extension": bool(_get_nested(draft, "ignore_extension")) if _get_nested(draft, "ignore_extension") is not None else None,
            "tmdb_id": tmdb_id,
            "tmdb_media_type": str(_get_nested(draft, "tmdb_media_type") or "").strip() or None,
        }

    def decorate_task_draft(self, draft: dict[str, Any], *, ctx: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(draft or {})
        shareurl = str(_get_nested(payload, "shareurl") or "").strip()
        stack = list((ctx or {}).get("share_browse_stack") or [])
        _set_nested(payload, "__share_folder_label__", _share_folder_label(shareurl, stack))
        _set_nested(payload, "__magic_rule_label__", _magic_rule_binding_label(payload))
        _set_nested(payload, "__tmdb_label__", _tmdb_binding_label(payload))
        return payload

    def auto_resolve_task_share_folder(self, db: Session, draft: dict[str, Any]) -> str | None:
        shareurl = str(_get_nested(draft, "shareurl") or "").strip()
        if not shareurl:
            return None
        root_shareurl = _task_shareurl(shareurl, "0")
        current_shareurl = shareurl
        chosen_account = str(_get_nested(draft, "account_name") or "").strip() or None
        preview_kwargs = self.task_preview_kwargs(draft)
        last_items: list[dict[str, Any]] = []
        for _depth in range(12):
            current_fid = _extract_task_share_fid(current_shareurl)
            payload = self.context.transport.actions(db).preview_task_share(
                shareurl=root_shareurl,
                account_name=chosen_account,
                pdir_fid=current_fid or None,
                max_items=50,
                **preview_kwargs,
            )
            items = list(payload.get("items") or [])
            last_items = items
            files = [x for x in items if not bool(x.get("is_dir"))]
            dirs = [x for x in items if bool(x.get("is_dir"))]
            if any(_is_video_preview_item(x) for x in files):
                return str(payload.get("resolved_shareurl") or current_shareurl).strip() or current_shareurl
            if len(dirs) == 1 and not files:
                current_shareurl = _task_shareurl(root_shareurl, str(dirs[0].get("fid") or ""))
                continue
            if len(dirs) > 1 and not files:
                picked = sorted(dirs, key=lambda x: _to_sort_ts(x.get("updated_at")), reverse=True)[0]
                current_shareurl = _task_shareurl(root_shareurl, str(picked.get("fid") or ""))
                continue
            break
        if any(_is_video_preview_item(x) for x in last_items if not bool(x.get("is_dir"))):
            return current_shareurl
        return None

    def apply_task_share_autofill(
        self,
        db: Session,
        draft: dict[str, Any],
        *,
        raw_text: str | None = None,
        selected_shareurl: str | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        shareurl = str(_get_nested(draft, "shareurl") or "").strip()
        if not shareurl:
            return draft, None
        parsed_text = str(raw_text or shareurl).strip() or shareurl
        picked_shareurl = str(selected_shareurl or shareurl).strip() or shareurl
        info = self.context.transport.actions(db).inspect_share_candidate(parsed_text, picked_shareurl)
        if not info or not bool(info.get("parsed")):
            return draft, str(info.get("preview_message") or "无法解析分享链接") if info else "无法解析分享链接"
        resolved_shareurl = str(info.get("resolved_shareurl") or info.get("shareurl") or shareurl).strip() or shareurl
        suggested_account_name = str(info.get("suggested_account_name") or "").strip()
        latest_video = dict(info.get("latest_video") or {})
        suggested_taskname = _suggest_taskname_from_share_text(parsed_text, resolved_shareurl, str(latest_video.get("name") or ""))
        if suggested_account_name:
            _set_nested(draft, "account_name", suggested_account_name)
        if suggested_taskname and not str(_get_nested(draft, "taskname") or "").strip():
            _set_nested(draft, "taskname", suggested_taskname)
        _set_nested(draft, "shareurl", resolved_shareurl)
        auto_resolved_shareurl = self.auto_resolve_task_share_folder(db, draft) or resolved_shareurl
        _set_nested(draft, "shareurl", auto_resolved_shareurl)
        _set_nested(draft, "__share_folder_label__", _share_folder_label(auto_resolved_shareurl))
        return draft, str(info.get("preview_message") or "").strip() or None

    def show_task_share_browser(self, db: Session, chat_id: int, *, message_id: int | None = None) -> None:
        session = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        ctx = dict(session.context or {})
        draft = dict(ctx.get("draft") or {})
        shareurl = str(_get_nested(draft, "shareurl") or "").strip()
        if not shareurl:
            raise ValueError("请先填写分享链接")
        root_shareurl = str(ctx.get("share_root_shareurl") or _task_shareurl(shareurl, "0")).strip() or shareurl
        stack = list(ctx.get("share_browse_stack") or [])
        current_pdir_fid = str((stack[-1] or {}).get("pdir_fid") or "").strip() if stack else str(_extract_task_share_fid(shareurl) or "").strip()
        payload = self.context.transport.actions(db).preview_task_share(
            shareurl=root_shareurl,
            account_name=_get_nested(draft, "account_name"),
            pdir_fid=current_pdir_fid or None,
            max_items=200,
            **self.task_preview_kwargs(draft),
        )
        current_shareurl = str(payload.get("resolved_shareurl") or _task_shareurl(root_shareurl, payload.get("pdir_fid"))).strip() or root_shareurl
        current_fid = str(payload.get("pdir_fid") or "").strip()
        if not stack:
            if current_fid and current_fid != "0":
                stack = [{"name": "当前目录", "pdir_fid": current_fid}]
            else:
                stack = []
        items = list(payload.get("items") or [])
        rows: list[list[dict[str, str]]] = []
        lines: list[str] = []
        current_folder_label = _share_folder_label(current_shareurl, stack)
        dir_items = [x for x in items if bool(x.get("is_dir"))]
        lines = [
            "选择分享目录",
            f"目录: {current_folder_label}",
            f"执行账号: {str(payload.get('suggested_account_name') or payload.get('account_name') or '自动')}",
        ]
        if stack:
            rows.append([button("⬆️ 上一级", "brw", "share", "up")])
        rows.append([button("✅ 选择当前目录", "brw", "share", "sel"), button("🔄 刷新", "brw", "share", "refresh")])
        for idx, item in enumerate(dir_items):
            rows.append([button(f"[DIR] {str(item.get('name') or '')[:26]}", "brw", "share", "open", idx)])
        preview_items = items[:12]
        if preview_items:
            lines.append("")
            lines.append("当前目录内容")
            for item in preview_items:
                if bool(item.get("is_dir")):
                    count = int(item.get("children_count") or item.get("include_items") or 0) or None
                    suffix = f" · {count} 项" if count is not None else ""
                    updated = str(item.get("updated_at_display") or _display_datetime(item.get("updated_at"))).strip()
                    time_text = f" · {updated}" if updated else ""
                    lines.append(f"📁 {str(item.get('name') or '-')}{suffix}{time_text}")
                else:
                    original = str(item.get("file_name") or item.get("name") or "-")
                    renamed = str(item.get("file_name_re") or item.get("file_name_saved") or "x")
                    size_text = file_size_text(item.get("size"))
                    updated = str(item.get("updated_at_display") or _display_datetime(item.get("updated_at"))).strip()
                    lines.append(f"📄 {_short_link_text(original, 52)}")
                    lines.append(f"   -> {_short_link_text(renamed, 52)}")
                    lines.append(f"   {size_text} · {updated}")
        ctx["share_dir_items"] = dir_items
        _set_nested(draft, "__share_folder_label__", current_folder_label)
        rows.append([button("✏️ 返回编辑器", "brw", "share", "back"), button("🏠 首页", "home")])
        mid = self.context.transport.edit_or_send(chat_id, "\n".join(lines), message_id=message_id or session.last_message_id, reply_markup=keyboard(rows))
        ctx["draft"] = draft
        ctx["share_root_shareurl"] = root_shareurl
        ctx["share_browse_shareurl"] = current_shareurl
        ctx["share_browse_stack"] = stack
        save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="task_share_browser", step="idle", context=ctx, last_message_id=mid)
        db.commit()

    def start_editor(
        self,
        db: Session,
        chat_id: int,
        *,
        kind: str,
        title: str,
        fields: list[dict[str, Any]],
        draft: dict[str, Any],
        target_id: int | None = None,
        message_id: int | None = None,
        extras: dict[str, Any] | None = None,
    ) -> None:
        active_fields = _editor_fields(fields)
        field_order = [str(x.get("key") or "") for x in active_fields]
        prefix = {"task": "tsk", "account": "acc"}[kind]
        if kind == "task":
            draft = self.decorate_task_draft(draft, ctx=extras)
        mid = self.context.transport.edit_or_send(
            chat_id,
            editor_message(title, draft, active_fields),
            message_id=message_id,
            reply_markup=editor_keyboard(prefix, target_id, active_fields, draft),
        )
        context = {"kind": kind, "draft": draft, "target_id": target_id or 0, "field_order": field_order}
        if extras:
            context.update(extras)
        save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene=f"{kind}_editor", step="idle", context=context, last_message_id=mid)
        db.commit()

    def dispatch_browser(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        session = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        ctx = dict(session.context or {})
        mode = parts[0] if parts else ""
        action = parts[1] if len(parts) > 1 else "refresh"
        if mode == "task":
            current_path = str(ctx.get("browse_path") or "/")
            if action == "up":
                ctx["browse_path"] = _remote_parent_path(current_path)
                save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, context=ctx)
                db.commit()
                self.show_task_drive_browser(db, chat_id, message_id=message_id)
                return
            if action == "open":
                idx = int(parts[2]) if len(parts) > 2 else -1
                payload = self.context.transport.actions(db).browse_task_drive(
                    dir_path=current_path,
                    account_name=_get_nested(ctx.get("draft") or {}, "account_name"),
                    shareurl=_get_nested(ctx.get("draft") or {}, "shareurl"),
                    max_items=20,
                )
                items = list(payload.get("items") or [])
                if idx < 0 or idx >= len(items):
                    raise ValueError("目录项不存在")
                item = items[idx]
                if not item.get("is_dir"):
                    raise ValueError("只能选择目录")
                next_path = f"{current_path.rstrip('/')}/{item.get('name')}".replace("//", "/") if current_path not in {"", "/"} else f"/{item.get('name')}"
                ctx["browse_path"] = next_path
                save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, context=ctx)
                db.commit()
                self.show_task_drive_browser(db, chat_id, message_id=message_id)
                return
            if action == "sel":
                draft = dict(ctx.get("draft") or {})
                _set_nested(draft, "savepath", current_path if current_path != "/" else "/")
                ctx["draft"] = draft
                ctx.pop("browse_path", None)
                save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="task_editor", step="idle", context=ctx, last_message_id=message_id)
                db.commit()
                self.start_editor(
                    db,
                    chat_id,
                    kind="task",
                    title=_editor_title("task", int(ctx.get("target_id") or 0) or None),
                    fields=TASK_FIELDS,
                    draft=draft,
                    target_id=int(ctx.get("target_id") or 0) or None,
                    message_id=message_id,
                    extras={k: v for k, v in ctx.items() if k != "draft"},
                )
                return
            if action in {"refresh", "back"}:
                if action == "back":
                    ctx.pop("browse_path", None)
                    save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="task_editor", step="idle", context=ctx, last_message_id=message_id)
                    db.commit()
                    self.start_editor(
                        db,
                        chat_id,
                        kind="task",
                        title=_editor_title("task", int(ctx.get("target_id") or 0) or None),
                        fields=TASK_FIELDS,
                        draft=dict(ctx.get("draft") or {}),
                        target_id=int(ctx.get("target_id") or 0) or None,
                        message_id=message_id,
                        extras={k: v for k, v in ctx.items() if k != "draft"},
                    )
                    return
                self.show_task_drive_browser(db, chat_id, message_id=message_id)
                return
        if mode in {"share", "start"}:
            draft = dict(ctx.get("draft") or {})
            root_shareurl = str(ctx.get("share_root_shareurl") or _task_shareurl(_get_nested(draft, "shareurl"), "0")).strip()
            current_shareurl = str(ctx.get("share_browse_shareurl") or _get_nested(draft, "shareurl") or "").strip()
            stack = list(ctx.get("share_browse_stack") or [])
            if action == "open":
                idx = int(parts[2]) if len(parts) > 2 else -1
                items = list(ctx.get("share_dir_items") or [])
                if idx < 0 or idx >= len(items):
                    raise ValueError("目录项不存在")
                item = dict(items[idx] or {})
                if not bool(item.get("is_dir")):
                    raise ValueError("只能选择目录")
                stack.append({"name": str(item.get("name") or ""), "pdir_fid": str(item.get("fid") or "")})
                ctx["share_browse_stack"] = stack
                ctx["share_browse_shareurl"] = _task_shareurl(root_shareurl, str(item.get("fid") or ""))
                save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, context=ctx)
                db.commit()
                self.show_task_share_browser(db, chat_id, message_id=message_id)
                return
            if action == "up":
                if stack:
                    stack.pop()
                target_fid = str((stack[-1] or {}).get("pdir_fid") or "0") if stack else "0"
                ctx["share_browse_stack"] = stack
                ctx["share_browse_shareurl"] = _task_shareurl(root_shareurl, target_fid)
                save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, context=ctx)
                db.commit()
                self.show_task_share_browser(db, chat_id, message_id=message_id)
                return
            if action == "sel":
                draft["shareurl"] = current_shareurl
                draft["__share_folder_label__"] = _share_folder_label(current_shareurl, stack)
                ctx["draft"] = draft
                for key in ("share_root_shareurl", "share_browse_shareurl", "share_browse_stack", "share_dir_items", "share_picker_mode"):
                    ctx.pop(key, None)
                save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="task_editor", step="idle", context=ctx, last_message_id=message_id)
                db.commit()
                self.start_editor(
                    db,
                    chat_id,
                    kind="task",
                    title=_editor_title("task", int(ctx.get("target_id") or 0) or None),
                    fields=TASK_FIELDS,
                    draft=draft,
                    target_id=int(ctx.get("target_id") or 0) or None,
                    message_id=message_id,
                    extras={k: v for k, v in ctx.items() if k != "draft"},
                )
                return
            if action in {"refresh", "back"}:
                if action == "back":
                    for key in ("share_root_shareurl", "share_browse_shareurl", "share_browse_stack", "share_dir_items", "share_picker_mode"):
                        ctx.pop(key, None)
                    save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="task_editor", step="idle", context=ctx, last_message_id=message_id)
                    db.commit()
                    self.start_editor(
                        db,
                        chat_id,
                        kind="task",
                        title=_editor_title("task", int(ctx.get("target_id") or 0) or None),
                        fields=TASK_FIELDS,
                        draft=dict(ctx.get("draft") or {}),
                        target_id=int(ctx.get("target_id") or 0) or None,
                        message_id=message_id,
                        extras={k: v for k, v in ctx.items() if k != "draft"},
                    )
                    return
                self.show_task_share_browser(db, chat_id, message_id=message_id)
                return
        raise ValueError("不支持的浏览动作")

    def dispatch_back(self, db: Session, chat_id: int, message_id: int) -> None:
        session = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        ctx = dict(session.context or {})
        step = str(session.step or "")
        scene = str(session.scene or "")
        if step == "await_field_input":
            kind = str(ctx.get("kind") or "")
            ctx.pop("field", None)
            if kind == "settings":
                save_session_data(
                    db,
                    chat_id=chat_id,
                    user_id=self.context.config.user_id,
                    scene="settings_domain",
                    step="idle",
                    context={"domain": str(ctx.get("domain") or ""), "page": int(ctx.get("page") or 1)},
                    last_message_id=message_id,
                )
                db.commit()
                self.context.settings_handlers.show_setting_domain(db, chat_id, str(ctx.get("domain") or ""), page=int(ctx.get("page") or 1), message_id=message_id)
                return
            if kind in {"task", "account"}:
                save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene=f"{kind}_editor", step="idle", context=ctx, last_message_id=message_id)
                db.commit()
                self.resume_editor(db, chat_id, kind=kind, ctx=ctx, message_id=message_id)
                return
        if step == "await_keyword" and scene == "search":
            replace_task_id = int(ctx.get("replace_task_id") or 0) or None
            if replace_task_id:
                save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="task_detail", step="idle", context=ctx, last_message_id=message_id)
                db.commit()
                self.context.task_handlers.show_task_detail(
                    db,
                    chat_id,
                    replace_task_id,
                    message_id=message_id,
                    back_page=int(ctx.get("page") or 1),
                    task_type=str(ctx.get("task_type") or "") or None,
                )
                return
            reset_session(db, chat_id=chat_id, user_id=self.context.config.user_id, preserve_message_id=False)
            db.commit()
            self.context.transport.show_home(db, chat_id, message_id=message_id)
            return
        if step == "await_auth_code":
            auth_session_id = str(ctx.get("auth_session_id") or "")
            if auth_session_id:
                info = self.context.transport.actions(db).get_account_auth_status(auth_session_id)
                self.context.account_handlers.show_account_auth(db, chat_id, info, message_id=message_id, back_page=int(ctx.get("page") or 1))
                return
        if step == "await_task_tmdb_keyword":
            save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="task_editor", step="idle", context=ctx, last_message_id=message_id)
            db.commit()
            self.resume_editor(db, chat_id, kind="task", ctx=ctx, message_id=message_id)
            return
        self.context.transport.show_home(db, chat_id, message_id=message_id)

    def dispatch_task_account_selector(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        session = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        ctx = dict(session.context or {})
        draft = dict(ctx.get("draft") or {})
        options = list(ctx.get("account_selector_options") or [])
        action = parts[0] if parts else "back"
        if action == "pick":
            token = str(parts[1] or "") if len(parts) > 1 else ""
            if token == "auto":
                _set_nested(draft, "account_name", "")
            else:
                idx = int(token) if token.isdigit() else -1
                if idx < 0 or idx >= len(options):
                    raise ValueError("账号不存在")
                _set_nested(draft, "account_name", str(options[idx] or ""))
            ctx["draft"] = draft
            ctx.pop("account_selector_options", None)
            save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="task_editor", step="idle", context=ctx, last_message_id=message_id)
            db.commit()
            self.start_editor(
                db,
                chat_id,
                kind="task",
                title=_editor_title("task", int(ctx.get("target_id") or 0) or None),
                fields=TASK_FIELDS,
                draft=draft,
                target_id=int(ctx.get("target_id") or 0) or None,
                message_id=message_id,
                extras={k: v for k, v in ctx.items() if k != "draft"},
            )
            return
        ctx.pop("account_selector_options", None)
        save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="task_editor", step="idle", context=ctx, last_message_id=message_id)
        db.commit()
        self.start_editor(
            db,
            chat_id,
            kind="task",
            title=_editor_title("task", int(ctx.get("target_id") or 0) or None),
            fields=TASK_FIELDS,
            draft=draft,
            target_id=int(ctx.get("target_id") or 0) or None,
            message_id=message_id,
            extras={k: v for k, v in ctx.items() if k != "draft"},
        )

    def dispatch_task_magic_rule_selector(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        session = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        ctx = dict(session.context or {})
        draft = dict(ctx.get("draft") or {})
        rules = list(ctx.get("magic_rule_options") or self.context.transport.actions(db).list_magic_regex_rules())
        action = parts[0] if parts else "back"
        if action == "list":
            page = int(parts[1]) if len(parts) > 1 and str(parts[1]).isdigit() else 1
            ctx["magic_rule_page"] = page
            save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, context=ctx, last_message_id=message_id)
            db.commit()
            self.show_task_magic_rule_selector(db, chat_id, message_id=message_id)
            return
        if action == "pick":
            idx = int(parts[1]) if len(parts) > 1 and str(parts[1]).isdigit() else -1
            if idx < 0 or idx >= len(rules):
                raise ValueError("内置规则不存在")
            rule = dict(rules[idx] or {})
            _set_nested(draft, "pattern", str(rule.get("pattern") or ""))
            _set_nested(draft, "replace", str(rule.get("replace") or ""))
            _set_nested(draft, "__magic_rule_key__", str(rule.get("key") or ""))
            _set_nested(draft, "__magic_rule_label__", _magic_rule_brief(rule))
            ctx["draft"] = draft
        elif action == "clear":
            _set_nested(draft, "pattern", "")
            _set_nested(draft, "replace", "")
            _set_nested(draft, "__magic_rule_key__", "")
            _set_nested(draft, "__magic_rule_label__", "")
            ctx["draft"] = draft
        ctx.pop("magic_rule_options", None)
        ctx.pop("magic_rule_page", None)
        save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="task_editor", step="idle", context=ctx, last_message_id=message_id)
        db.commit()
        self.resume_task_editor(db, chat_id, ctx=ctx, message_id=message_id)

    def handle_editor_field_click(
        self,
        db: Session,
        chat_id: int,
        message_id: int,
        *,
        kind: str,
        fields: list[dict[str, Any]],
        target_id: int | None,
        field: str,
    ) -> None:
        session = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        ctx = dict(session.context or {})
        draft = dict(ctx.get("draft") or {})
        if kind == "task" and field == "account_name":
            self.show_task_account_selector(db, chat_id, message_id=message_id)
            return
        if kind == "task" and field == "savepath":
            self.show_task_drive_browser(db, chat_id, message_id=message_id)
            return
        if kind == "task" and field == "__share_folder__":
            self.show_task_share_browser(db, chat_id, message_id=message_id)
            return
        if kind == "task" and field == "__magic_rule__":
            self.show_task_magic_rule_selector(db, chat_id, message_id=message_id)
            return
        if kind == "task" and field == "__tmdb_bind__":
            self.context.search_handlers.show_task_tmdb_prompt(db, chat_id, message_id=message_id)
            return
        meta = _field_meta(fields, field)
        field_type = meta["type"]
        if field_type == "bool":
            current = bool(_get_nested(draft, field))
            _set_nested(draft, field, not current)
            ctx["draft"] = draft
            save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, context=ctx)
            db.commit()
            self.start_editor(
                db,
                chat_id,
                kind=kind,
                title=_editor_title(kind, target_id),
                fields=fields,
                draft=draft,
                target_id=target_id,
                message_id=message_id,
                extras={k: v for k, v in ctx.items() if k != "draft"},
            )
            return
        if field_type in {"enum", "enum_dynamic"}:
            options = list(meta.get("options") or [])
            if field_type == "enum_dynamic":
                options = list(ctx.get(str(meta.get("options_key") or "")) or [])
            if not options:
                raise ValueError("没有可用选项")
            current = _get_nested(draft, field)
            try:
                idx = options.index(current)
            except ValueError:
                idx = -1
            _set_nested(draft, field, options[(idx + 1) % len(options)])
            ctx["draft"] = draft
            save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, context=ctx)
            db.commit()
            self.start_editor(
                db,
                chat_id,
                kind=kind,
                title=_editor_title(kind, target_id),
                fields=fields,
                draft=draft,
                target_id=target_id,
                message_id=message_id,
                extras={k: v for k, v in ctx.items() if k != "draft"},
            )
            return
        prompt = _field_prompt(meta)
        save_session_data(
            db,
            chat_id=chat_id,
            user_id=self.context.config.user_id,
            step="await_field_input",
            context={**ctx, "draft": draft, "field": field, "kind": kind, "target_id": target_id or 0},
            last_message_id=message_id,
        )
        db.commit()
        self.context.transport.show_back_cancel_home(chat_id, prompt, message_id=message_id)

    def handle_editor_save(self, db: Session, chat_id: int, message_id: int, *, kind: str, fields: list[dict[str, Any]], target_id: int | None) -> None:
        session = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        ctx = dict(session.context or {})
        draft = dict(ctx.get("draft") or {})
        if kind == "task":
            _set_nested(draft, "task_type", "drama")
        payload = _collapse_payload(fields, draft)
        actions = self.context.transport.actions(db)
        if kind == "task":
            item = actions.save_task(task_id=target_id, payload=payload)
            self.context.task_handlers.show_task_detail(
                db,
                chat_id,
                int(item["id"]),
                message_id=message_id,
                back_page=int(ctx.get("page") or 1),
                task_type=str(ctx.get("task_type") or "") or None,
            )
            return
        if kind == "account":
            item = actions.save_account(account_id=target_id, payload=payload)
            self.context.account_handlers.show_account_detail(db, chat_id, int(item["id"]), message_id=message_id, back_page=int(ctx.get("page") or 1))
            return

    def handle_field_input(self, db: Session, chat_id: int, user_id: int, text: str, session) -> None:
        ctx = dict(session.context or {})
        field = str(ctx.get("field") or "")
        kind = str(ctx.get("kind") or "")
        if kind == "settings":
            domain = str(ctx.get("domain") or "")
            current_domain = self.context.transport.actions(db).get_setting_domain(domain)
            current_values = current_domain.get("values") or {}
            current = current_values.get(field)
            meta = _setting_field_meta(domain, field, current)
            value = _parse_scalar(text, str(meta.get("type") or "str"))
            self.context.transport.actions(db).update_setting_value(domain, field, value)
            self.context.settings_handlers.show_setting_domain(db, chat_id, domain, page=int(ctx.get("page") or 1), message_id=session.last_message_id)
            return
        if kind == "auth":
            self.context.account_handlers.handle_auth_code_input(db, chat_id, text, session)
            return
        target_id = int(ctx.get("target_id") or 0) or None
        draft = dict(ctx.get("draft") or {})
        fields = TASK_FIELDS if kind == "task" else ACCOUNT_FIELDS
        meta = _field_meta(fields, field)
        try:
            value = _parse_scalar(text, meta["type"])
        except Exception as exc:
            self.context.transport.send(chat_id, f"输入无效: {exc}")
            return
        _set_nested(draft, field, value)
        if kind == "task" and field == "shareurl":
            draft, _message = self.apply_task_share_autofill(db, draft)
            for key in ("share_root_shareurl", "share_browse_shareurl", "share_browse_stack", "share_dir_items", "share_picker_mode"):
                ctx.pop(key, None)
        if kind == "task" and field in {"pattern", "replace"}:
            if str(_get_nested(draft, "__magic_rule_key__") or "").strip():
                _set_nested(draft, "__magic_rule_key__", "")
                _set_nested(draft, "__magic_rule_label__", "已手动修改")
        ctx["draft"] = draft
        ctx.pop("field", None)
        save_session_data(db, chat_id=chat_id, user_id=user_id, step="idle", context=ctx)
        db.commit()
        self.start_editor(
            db,
            chat_id,
            kind=kind,
            title=_editor_title(kind, target_id),
            fields=fields,
            draft=draft,
            target_id=target_id,
            message_id=session.last_message_id,
            extras={k: v for k, v in ctx.items() if k != "draft"},
        )
