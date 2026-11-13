import importlib
from types import SimpleNamespace


def test_editor_keeps_scalar_parsing_and_draft_state(monkeypatch):
    module = importlib.import_module('app.application.telegram_bot.editor')
    received = []
    context = SimpleNamespace(
        config=SimpleNamespace(user_id=42),
        transport=SimpleNamespace(actions=lambda db: SimpleNamespace()),
    )
    editor = module.EditorHandlers(context)
    monkeypatch.setattr(module, 'save_session_data', lambda *args, **kwargs: None)
    monkeypatch.setattr(editor, 'start_editor', lambda *args, **kw: received.append(kw))
    session = SimpleNamespace(
        scene='task_edit', step='await_field_input', last_message_id=7,
        context={'kind': 'task', 'field': 'taskname', 'target_id': 3, 'draft': {'taskname': '旧名'}},
    )
    editor.handle_field_input(SimpleNamespace(commit=lambda: None), 42, 42, '新名称', session)
    assert received and received[0]['draft']['taskname'] == '新名称'
