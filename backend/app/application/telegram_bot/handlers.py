from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from app.application.telegram_bot.client import TelegramBotClient
from app.application.telegram_bot.config import TelegramBotConfig
from app.application.telegram_bot.context import BotContext
from app.application.telegram_bot.transport import BotTransport
from app.application.telegram_bot.task_handlers import TaskHandlers
from app.application.telegram_bot.account_handlers import AccountHandlers
from app.application.telegram_bot.settings_handlers import SettingsHandlers
from app.application.telegram_bot.search_handlers import SearchHandlers
from app.application.telegram_bot.dispatch import BotDispatch
from app.application.telegram_bot.editor import EditorHandlers


@dataclass
class TelegramBotHandler:
    """Compose independent interaction components and forward incoming updates."""
    client: TelegramBotClient
    config: TelegramBotConfig
    context: BotContext = field(init=False, repr=False)

    def __post_init__(self) -> None:
        context = BotContext(client=self.client, config=self.config)
        context.transport = BotTransport(context)
        context.task_handlers = TaskHandlers(context)
        context.account_handlers = AccountHandlers(context)
        context.settings_handlers = SettingsHandlers(context)
        context.search_handlers = SearchHandlers(context)
        context.dispatch = BotDispatch(context)
        context.editor = EditorHandlers(context)
        self.context = context

    def handle_update(self, update: dict[str, Any]) -> None:
        self.context.dispatch.handle_update(update)
