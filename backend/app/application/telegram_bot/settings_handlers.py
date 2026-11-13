from __future__ import annotations

from sqlalchemy.orm import Session
from app.application.telegram_bot.formatters import button, keyboard, setting_domain_keyboard, setting_domain_message, settings_domains_keyboard, settings_domains_message
from app.application.telegram_bot.session_store import load_session_data, save_session_data
from app.application.telegram_bot.context import BotContext
from app.application.telegram_bot.editor_helpers import (_field_prompt, _setting_display_values, _setting_field_meta, _setting_fields)


class SettingsHandlers:
    def __init__(self, context: BotContext):
        self.context = context

    def show_settings_menu(self, db: Session, chat_id: int, *, message_id: int | None = None) -> None:
        domains = self.context.transport.actions(db).list_setting_domains()
        mid = self.context.transport.edit_or_send(chat_id, settings_domains_message(domains), message_id=message_id, reply_markup=settings_domains_keyboard(domains))
        save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="settings", step="idle", context={}, last_message_id=mid)
        db.commit()

    def show_setting_domain(self, db: Session, chat_id: int, domain_key: str, *, page: int = 1, message_id: int | None = None) -> None:
        domain = self.context.transport.actions(db).get_setting_domain(domain_key)
        values = domain.get("values") or {}
        display_values = _setting_display_values(domain_key, values)
        fields, total, page_size = _setting_fields(self.context.transport.actions(db), domain_key, values, page=page)
        mid = self.context.transport.edit_or_send(
            chat_id,
            setting_domain_message(domain, fields, values=display_values),
            message_id=message_id,
            reply_markup=setting_domain_keyboard(
                domain_key,
                fields,
                display_values,
                page=page,
                total=total,
                page_size=page_size,
                test_notify=(domain_key == "notifications"),
            ),
        )
        save_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id, scene="settings_domain", step="idle", context={"domain": domain_key, "page": page}, last_message_id=mid)
        db.commit()

    def dispatch_settings(self, db: Session, chat_id: int, message_id: int, parts: list[str]) -> None:
        action = parts[0] if parts else "menu"
        if action == "domain":
            domain = parts[1]
            page = int(parts[2]) if len(parts) > 2 and str(parts[2]).isdigit() else 1
            self.show_setting_domain(db, chat_id, domain, page=page, message_id=message_id)
        elif action == "field":
            domain = parts[1]
            field = parts[2]
            self.handle_settings_field_click(db, chat_id, message_id, domain, field)
        elif action == "testnotify":
            current = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
            current_page = int((current.context or {}).get("page") or 1)
            results = self.context.transport.actions(db).send_notification_test("TG 测试通知", "这是一条来自 TG 控制台的测试消息。")
            lines = ["通知测试结果"] + [f"{x.get('channel')}: {'ok' if x.get('ok') else x.get('error') or 'failed'}" for x in results]
            self.context.transport.edit_or_send(
                chat_id,
                "\n".join(lines),
                message_id=message_id,
                reply_markup=keyboard([[button("⚙️ 返回通知配置", "cfg", "domain", "notifications", current_page)], [button("🏠 首页", "home")]]),
            )
        else:
            self.show_settings_menu(db, chat_id, message_id=message_id)

    def handle_settings_field_click(self, db: Session, chat_id: int, message_id: int, domain: str, field: str) -> None:
        session = load_session_data(db, chat_id=chat_id, user_id=self.context.config.user_id)
        page = int((session.context or {}).get("page") or 1)
        domain_data = self.context.transport.actions(db).get_setting_domain(domain)
        values = domain_data.get("values") or {}
        current = values.get(field)
        meta = _setting_field_meta(domain, field, current)
        if str(meta.get("type") or "") == "bool":
            self.context.transport.actions(db).update_setting_value(domain, field, not current)
            self.show_setting_domain(db, chat_id, domain, page=page, message_id=message_id)
            return
        save_session_data(
            db,
            chat_id=chat_id,
            user_id=self.context.config.user_id,
            scene="settings_input",
            step="await_field_input",
            context={"domain": domain, "field": field, "kind": "settings", "page": page},
            last_message_id=message_id,
        )
        db.commit()
        self.context.transport.show_back_cancel_home(chat_id, _field_prompt(meta), message_id=message_id)
