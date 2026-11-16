from __future__ import annotations


from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.application.container import adapter_factory as _acc_AdapterFactory; AdapterFactory = _acc_AdapterFactory()
from app.application.container import account_manager_cls as _acc_DatabaseAccountManager; DatabaseAccountManager = _acc_DatabaseAccountManager()
from app.application.container import adapter_registry as _acc_AdapterRegistry; AdapterRegistry = _acc_AdapterRegistry()
from app.application.drive_account_probe_scheduler import (
    get_or_create_drive_account_probe_scheduler_setting,
    update_drive_account_probe_scheduler_setting,
)
from app.application.notifications.sender import send_test
from app.application.notifications import sender
from app.application.notifications.settings import (
    get_or_create_notification_setting,
    get_runtime_notification_config,
    update_notification_setting,
)
from app.application.task_scheduler import get_or_create_task_scheduler_setting, update_task_scheduler_setting
from app.application.tmdb_settings import get_or_create_tmdb_setting, get_tmdb_runtime_config, load_tmdb_config, update_tmdb_setting


from app.application.telegram_bot.action_helpers import _dump_dt, _loads

@dataclass
class TelegramSettingActions:
    db: Session

    def list_setting_domains(self) -> list[dict[str, str]]:
        return [
            {"key": "notifications", "label": "通知配置"},
            {"key": "task_scheduler", "label": "任务调度"},
            {"key": "probe_scheduler", "label": "账号探测调度"},
            {"key": "tmdb", "label": "TMDB"},
        ]


    def get_setting_domain(self, key: str) -> dict[str, Any]:
        if key == "notifications":
            item = get_or_create_notification_setting(self.db)
            current = get_runtime_notification_config(self.db)
            return {
                "key": key,
                "label": "通知配置",
                "values": {**dict(sender.DEFAULT_PUSH_CONFIG), **current},
                "default_values": dict(sender.DEFAULT_PUSH_CONFIG),
                "updated_at": _dump_dt(getattr(item, "updated_at", None)),
            }
        if key == "task_scheduler":
            item = get_or_create_task_scheduler_setting(self.db)
            return {"key": key, "label": "任务调度", "values": {"enabled": bool(item.enabled), "crontab": str(item.crontab), "timezone": str(item.timezone)}}
        if key == "probe_scheduler":
            item = get_or_create_drive_account_probe_scheduler_setting(self.db)
            return {
                "key": key,
                "label": "账号探测调度",
                "values": {
                    "enabled": bool(item.enabled),
                    "crontab": str(item.crontab),
                    "timezone": str(item.timezone),
                    "enabled_only": bool(item.enabled_only),
                },
            }
        if key == "tmdb":
            item = get_or_create_tmdb_setting(self.db)
            runtime = get_tmdb_runtime_config(item)
            values = load_tmdb_config(item)
            values["api_key"] = runtime.get("api_key") or ""
            return {"key": key, "label": "TMDB", "values": values}
        raise ValueError(f"unsupported setting domain: {key}")


    def update_setting_value(self, domain: str, field: str, value: Any) -> dict[str, Any]:
        if domain == "notifications":
            current = get_runtime_notification_config(self.db)
            current[str(field)] = value
            update_notification_setting(self.db, config=current)
        elif domain == "task_scheduler":
            payload = {field: value}
            update_task_scheduler_setting(self.db, **payload)
        elif domain == "probe_scheduler":
            payload = {field: value}
            update_drive_account_probe_scheduler_setting(self.db, **payload)
        elif domain == "tmdb":
            update_tmdb_setting(self.db, payload={field: value})
        else:
            raise ValueError(f"unsupported setting domain: {domain}")
        self.db.commit()
        return self.get_setting_domain(domain)


    def send_notification_test(self, title: str, content: str, channels: list[str] | None = None) -> list[dict[str, Any]]:
        config = get_runtime_notification_config(self.db)
        return send_test(title, content, config=config, channels=channels)


    def raw_notification_keys(self) -> list[str]:
        item = get_or_create_notification_setting(self.db)
        current = get_runtime_notification_config(self.db)
        saved = _loads(getattr(item, "config_json", None))
        return sorted({*sender.DEFAULT_PUSH_CONFIG.keys(), *saved.keys(), *current.keys()})

