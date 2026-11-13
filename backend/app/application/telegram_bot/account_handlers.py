from __future__ import annotations

import base64
from typing import Any
from sqlalchemy.orm import Session
from app.application.telegram_bot.formatters import account_detail_keyboard, account_detail_message, accounts_message, button, keyboard, pagination_row
from app.application.telegram_bot.session_store import load_session_data, save_session_data
from app.application.telegram_bot.context import BotContext
from app.application.telegram_bot.editor_helpers import (ACCOUNT_FIELDS, _flatten_seed, _set_nested)
import logging
logger = logging.getLogger(__name__)


class AccountHandlers:
    def __init__(self, context: BotContext):
        self.context = context

    def show_accounts(self, db: Session, chat_id: int, *, page: int = 1, message_id: int | None = None) -> None:
        payload = self.context.transport.actions(db).list_accounts(page=page)
        rows = [[button(f"#{item['id']} {item['name'][:20]}", "acc", "detail", item["id"])] for item in payload["items"]]
        rows.append([button("➕ 新建账号", "acc", "new"), button("🔄 刷新资料", "acc", "refresh")])
        rows.append(pagination_row("acc:list", payload["page"], payload["total"], payload["page_size"]))
        mid = self.context.transport.edit_or_send(chat_id, accounts_message(payload), message_id=message_id, reply_markup=keyboard(rows))
        save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="accounts", step="idle", context={"page": page}, last_message_id=mid)
        db.commit()

    def show_account_detail(self, db: Session, chat_id: int, account_id: int, *, message_id: int | None = None, back_page: int = 1) -> None:
        item = self.context.transport.actions(db).get_account_detail(account_id)
        mid = self.context.transport.edit_or_send(
            chat_id,
            account_detail_message(item),
            message_id=message_id,
            reply_markup=account_detail_keyboard(account_id, bool(item.get("enabled")), bool(item.get("is_default")), back_page=back_page),
        )
        save_session_data(
            db,
            chat_id=chat_id,
            user_id=self.context.config.user_id,
            scene="account_detail",
            step="idle",
            context={"account_id": account_id, "page": back_page},
            last_message_id=mid,
        )
        db.commit()

    def show_account_auth(self, db: Session, chat_id: int, auth_payload: dict[str, Any], *, message_id: int | None = None, back_page: int = 1) -> None:
        account_id = int(auth_payload.get("account_id") or 0)
        method = str(auth_payload.get("method") or "")
        payload = auth_payload.get("payload") or {}
        rows: list[list[dict[str, str]]] = []
        lines = [f"账号 #{account_id} 认证", f"方式: {method or '-'}"]
        if method == "qrcode":
            lines.append(f"二维码链接: {payload.get('qrcode_url') or '-'}")
            lines.append(f"状态: {payload.get('status') or 'NEW'}")
            if payload.get("message"):
                lines.append(f"说明: {payload.get('message')}")
            rows.append([button("🔄 轮询状态", "auth", "poll"), button("🔁 重新开始", "auth", "restart")])
        elif method == "sms":
            lines.append(f"手机号: {payload.get('mobile') or '-'}")
            if payload.get("show_name"):
                lines.append(f"账号: {payload.get('show_name')}")
            rows.append([button("📨 发送短信", "auth", "smssend"), button("🔢 输入验证码", "auth", "input")])
        elif method == "captcha":
            lines.append("已发送验证码图片，请直接回复验证码。")
            rows.append([button("🔢 输入验证码", "auth", "input"), button("🔁 重新开始", "auth", "restart")])
            image_base64 = str(payload.get("image_base64") or "")
            if image_base64:
                try:
                    self.context.transport.send_photo(chat_id, base64.b64decode(image_base64), caption="cloud189 图形验证码")
                except Exception:
                    logger.exception("telegram captcha image send failed")
        elif method == "done":
            lines.append("认证已完成，账号状态已更新。")
        else:
            lines.append(str(auth_payload.get("message") or "当前账号无需额外认证"))
        rows.append([button("👤 返回账号详情", "acc", "detail", account_id), button("🏠 首页", "home")])
        mid = self.context.transport.edit_or_send(chat_id, "\n".join(lines), message_id=message_id, reply_markup=keyboard(rows))
        save_session_data(
            db,
            chat_id=chat_id,
            user_id=self.context.config.user_id,
            scene="account_auth",
            step="idle",
            context={"account_id": account_id, "auth_session_id": auth_payload.get("session_id") or "", "auth_method": method, "page": back_page},
            last_message_id=mid,
        )
        db.commit()

    def dispatch_account(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        session = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        ctx = dict(session.context or {})
        action = parts[0] if parts else "list"
        if action == "list":
            page = int(parts[1]) if len(parts) > 1 and str(parts[1]).isdigit() else 1
            self.show_accounts(db, chat_id, page=page, message_id=message_id)
        elif action == "detail":
            self.show_account_detail(db, chat_id, int(parts[1]), message_id=message_id, back_page=int(ctx.get("page") or 1))
        elif action == "default":
            item = self.context.transport.actions(db).set_account_default(int(parts[1]))
            self.show_account_detail(db, chat_id, int(item["id"]), message_id=message_id, back_page=int(ctx.get("page") or 1))
        elif action == "toggle":
            item = self.context.transport.actions(db).toggle_account(int(parts[1]))
            self.show_account_detail(db, chat_id, int(item["id"]), message_id=message_id, back_page=int(ctx.get("page") or 1))
        elif action == "probe":
            item = self.context.transport.actions(db).probe_account(int(parts[1]))
            self.show_account_detail(db, chat_id, int(item["id"]), message_id=message_id, back_page=int(ctx.get("page") or 1))
        elif action == "signin":
            account_id = int(parts[1])
            result = self.context.transport.actions(db).sign_in_account(account_id)
            lines = [f"账号 #{account_id} 签到结果", str(result)]
            self.context.transport.edit_or_send(chat_id, "\n".join(lines), message_id=message_id, reply_markup=keyboard([[button("👤 返回账号详情", "acc", "detail", account_id)], [button("🏠 首页", "home")]]))
        elif action == "auth":
            account_id = int(parts[1])
            payload = self.context.transport.actions(db).start_account_auth(account_id)
            if payload.get("method") == "done":
                self.show_account_detail(db, chat_id, account_id, message_id=message_id, back_page=int(ctx.get("page") or 1))
            else:
                self.show_account_auth(db, chat_id, payload, message_id=message_id, back_page=int(ctx.get("page") or 1))
        elif action == "refresh":
            items = self.context.transport.actions(db).refresh_accounts()
            self.context.transport.edit_or_send(chat_id, f"已刷新 {len(items)} 个账号资料", message_id=message_id, reply_markup=keyboard([[button("👥 返回账号列表", "acc", "list", 1)], [button("🏠 首页", "home")]]))
        elif action == "edit":
            account_id = int(parts[1])
            item = self.context.transport.actions(db).get_account_detail(account_id)
            draft = _flatten_seed(ACCOUNT_FIELDS, item)
            drive_types = [x.get("code") for x in self.context.transport.actions(db).get_drive_types()]
            self.context.editor.start_editor(
                db,
                chat_id,
                kind="account",
                title=f"编辑账号 #{account_id}",
                fields=ACCOUNT_FIELDS,
                draft=draft,
                target_id=account_id,
                message_id=message_id,
                extras={"drive_types": [x for x in drive_types if x], "page": int(ctx.get("page") or 1)},
            )
        elif action == "new":
            drive_types = [x.get("code") for x in self.context.transport.actions(db).get_drive_types()]
            draft = _flatten_seed(ACCOUNT_FIELDS)
            if drive_types:
                _set_nested(draft, "drive_type", drive_types[0])
            self.context.editor.start_editor(
                db,
                chat_id,
                kind="account",
                title="新建账号",
                fields=ACCOUNT_FIELDS,
                draft=draft,
                message_id=message_id,
                extras={"drive_types": [x for x in drive_types if x], "page": int(ctx.get("page") or 1)},
            )
        elif action == "field":
            target_id = None if parts[1] == "new" else int(parts[1])
            field = parts[2]
            self.context.editor.handle_editor_field_click(db, chat_id, message_id, kind="account", fields=ACCOUNT_FIELDS, target_id=target_id, field=field)
        elif action == "save":
            target_id = None if parts[1] == "new" else int(parts[1])
            self.context.editor.handle_editor_save(db, chat_id, message_id, kind="account", fields=ACCOUNT_FIELDS, target_id=target_id)

    def dispatch_auth(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        session = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        ctx = dict(session.context or {})
        account_id = int(ctx.get("account_id") or 0)
        auth_session_id = str(ctx.get("auth_session_id") or "")
        page = int(ctx.get("page") or 1)
        action = parts[0] if parts else "status"
        if action == "restart":
            payload = self.context.transport.actions(db).start_account_auth(account_id)
            self.show_account_auth(db, chat_id, payload, message_id=message_id, back_page=page)
            return
        if not auth_session_id:
            raise ValueError("认证会话已失效")
        if action == "poll":
            result = self.context.transport.actions(db).poll_account_auth_qrcode(auth_session_id)
            if result.get("done"):
                self.show_account_detail(db, chat_id, int(result["account"]["id"]), message_id=message_id, back_page=page)
            else:
                self.show_account_auth(db, chat_id, result, message_id=message_id, back_page=page)
            return
        if action == "smssend":
            result = self.context.transport.actions(db).send_account_auth_sms(auth_session_id)
            info = self.context.transport.actions(db).get_account_auth_status(auth_session_id)
            info["payload"] = {**dict(info.get("payload") or {}), "message": str(result.get("message") or "短信已发送")}
            self.show_account_auth(db, chat_id, info, message_id=message_id, back_page=page)
            return
        if action == "input":
            save_session_data(
                db,
                chat_id=chat_id,
                user_id=self.context.config.user_id,
                scene="account_auth_input",
                step="await_auth_code",
                context=ctx,
                last_message_id=message_id,
            )
            db.commit()
            self.context.transport.show_back_cancel_home(chat_id, "请输入认证验证码。", message_id=message_id)
            return

    def handle_auth_code_input(self, db: Session, chat_id: int, text: str, session) -> None:
        ctx = dict(session.context or {})
        auth_session_id = str(ctx.get("auth_session_id") or "")
        if not auth_session_id:
            raise ValueError("认证会话已失效")
        result = self.context.transport.actions(db).submit_account_auth_code(auth_session_id, text)
        if result.get("done"):
            self.show_account_detail(db, chat_id, int(result["account"]["id"]), message_id=session.last_message_id, back_page=int(ctx.get("page") or 1))
            return
        self.show_account_auth(db, chat_id, result, message_id=session.last_message_id, back_page=int(ctx.get("page") or 1))
