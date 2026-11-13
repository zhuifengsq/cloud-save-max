import importlib
from types import SimpleNamespace
from unittest.mock import Mock


def test_unauthorized_message_does_not_open_database_or_send(monkeypatch):
    from app.application.telegram_bot.handlers import TelegramBotHandler
    client = Mock()
    handler = TelegramBotHandler(client=client, config=SimpleNamespace(user_id=42))
    handler.handle_update({'message': {'chat': {'id': 7}, 'from': {'id': 7}, 'text': '/tasks'}})
    assert client.mock_calls == []


def test_task_interaction_can_run_without_bot_or_real_network(monkeypatch):
    module = importlib.import_module('app.application.telegram_bot.task_handlers')
    calls = []
    actions = SimpleNamespace(list_tasks=lambda **kw: {
        'items': [{'id': 7, 'taskname': '测试剧'}], 'page': 1, 'page_size': 10, 'total': 1,
    })
    transport = SimpleNamespace(
        actions=lambda db: actions,
        edit_or_send=lambda chat, text, **kw: calls.append((text, kw)) or 10,
    )
    context = SimpleNamespace(config=SimpleNamespace(user_id=42), transport=transport)
    sessions = []
    monkeypatch.setattr(module, 'save_session_data', lambda db, **kw: sessions.append(kw))
    db = SimpleNamespace(commit=lambda: None)
    module.TaskHandlers(context).show_tasks(db, 42)
    assert '测试剧' in calls[0][0]
    buttons = calls[0][1]['reply_markup']['inline_keyboard']
    assert any(button.get('callback_data') == 'tsk:detail:7' for row in buttons for button in row)
    assert sessions[0]['scene'] == 'tasks' and sessions[0]['last_message_id'] == 10

