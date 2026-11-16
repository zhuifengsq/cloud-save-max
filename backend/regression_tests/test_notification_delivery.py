"""Notification behavior before and after the F2 module move; no real transport."""
import pytest

from app.application.notifications import channel_delivery as delivery
from app.application.notifications import sender
from app.application.notifications.queue import NotifyQueue
from app.core.settings import settings


CHANNELS = {
    'bark': 'bark', 'dingding_bot': 'dingding', 'feishu_bot': 'feishu',
    'go_cqhttp': 'gocqhttp', 'gotify': 'gotify', 'iGot': 'igot', 'serverJ': 'serverj',
    'pushdeer': 'pushdeer', 'chat': 'synology_chat', 'pushplus_bot': 'pushplus',
    'weplus_bot': 'weplus', 'qmsg_bot': 'qmsg', 'wecom_app': 'wecom_app',
    'wecom_bot': 'wecom_bot', 'telegram_bot': 'telegram', 'aibotk': 'aibotk',
    'smtp': 'smtp', 'pushme': 'pushme', 'chronocat': 'chronocat', 'dodo_bot': 'dodo',
    'custom_notify': 'webhook', 'ntfy': 'ntfy', 'wxpusher_bot': 'wxpusher',
}


def configured():
    return {key: ('fake-'+key if isinstance(value, str) else value)
            for key, value in delivery.DEFAULT_PUSH_CONFIG.items()}


@pytest.mark.parametrize('function,channel', CHANNELS.items())
def test_every_channel_receives_configuration_and_payload_once(monkeypatch, function, channel):
    calls = []
    config = configured()
    config['__channel_enabled'] = {channel: True}
    def transport(title, content):
        calls.append((title, content, dict(delivery.push_config)))
        return {'channel': channel, 'ok': True}
    transport.__name__ = function
    monkeypatch.setattr(delivery, function, transport)
    result = delivery.send('title', 'payload', ignore_default_config=True, channels=[channel], **config)
    assert len(result) == 1 and result[0]['channel'] == channel
    assert calls == [('title', 'payload', config)]


def test_disabled_channel_and_transport_error_are_reported(monkeypatch):
    config = configured()
    config['__channel_enabled'] = {'bark': False}
    assert delivery.send('title', 'body', True, ['bark'], **config) == []
    def broken(*args):
        raise RuntimeError('transport unavailable')
    broken.__name__ = 'bark'
    monkeypatch.setattr(delivery, 'bark', broken)
    config['__channel_enabled'] = {'bark': True}
    results = delivery.send('title', 'body', True, ['bark'], **config)
    assert results[0]['ok'] is False


def test_runtime_sync_exception_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(settings, 'use_notify_queue', False)
    monkeypatch.setattr(sender, 'get_or_create_notification_setting', lambda db: object())
    monkeypatch.setattr(sender, 'load_notification_config', lambda row: {})
    def broken(*args, **kwargs):
        raise RuntimeError('transport failure')
    monkeypatch.setattr(delivery, 'send', broken)
    assert sender.send_runtime(object(), 'title', 'body') == []
    assert 'notification' in caplog.text.lower() or '通知' in caplog.text


def test_runtime_queue_full_does_not_send_synchronously(monkeypatch):
    monkeypatch.setattr(settings, 'use_notify_queue', True)
    monkeypatch.setattr(sender, 'get_or_create_notification_setting', lambda db: object())
    monkeypatch.setattr(sender, 'load_notification_config', lambda row: {})
    calls = []
    queue = NotifyQueue(maxsize=1, autostart=False, handler=lambda *args: calls.append(args))
    monkeypatch.setattr(sender, 'get_notify_queue', lambda: queue)
    assert sender.send_runtime(object(), 'first', 'body') == []
    assert sender.send_runtime(object(), 'second', 'body') == []
    assert calls == []
    queue.start()
    assert queue.join(5)
    assert [item[0] for item in calls] == ['first']


def test_test_notification_is_sync_runtime_is_queued_once(monkeypatch):
    monkeypatch.setattr(settings, 'use_notify_queue', True)
    monkeypatch.setattr(sender, 'get_or_create_notification_setting', lambda db: object())
    monkeypatch.setattr(sender, 'load_notification_config', lambda row: {'BARK_PUSH': 'fake'})
    calls = []
    def send(*args, **kwargs):
        calls.append((args, kwargs))
        return [{'channel': 'bark', 'ok': True}]
    monkeypatch.setattr(delivery, 'send', send)
    queue = NotifyQueue(handler=lambda t, c, ch, kw: delivery.send(t, c, channels=ch, **kw), autostart=False)
    monkeypatch.setattr(sender, 'get_notify_queue', lambda: queue)
    assert sender.send_test('test', 'body', config={'BARK_PUSH': 'fake'}, channels=['bark'])[0]['ok']
    assert len(calls) == 1
    sender.send_runtime(object(), 'runtime', 'body', channels=['bark'])
    assert len(calls) == 1
    queue.start()
    assert queue.join(5)
    assert [item[0][0] for item in calls] == ['test', 'runtime']
    assert calls[1][1]['BARK_PUSH'] == 'fake'
    assert calls[1][1]['channels'] == ['bark']


@pytest.mark.parametrize('function,channel', [(f, c) for f, c in CHANNELS.items() if f != 'smtp'])
def test_real_channel_formats_title_body_and_uses_configured_transport(monkeypatch, function, channel):
    import json
    from types import SimpleNamespace
    calls = []
    config = configured()
    config.update(HITOKOTO=False, GOBOT_URL='https://fake.invalid/send_group_msg',
                  QYWX_AM='corp,secret,agent,user', WEBHOOK_URL='https://fake.invalid/$title',
                  WEBHOOK_BODY='$content', WEBHOOK_HEADERS='{}', WEBHOOK_METHOD='POST',
                  WXPUSHER_TOPIC_IDS='123;456', WXPUSHER_UIDS='fake-uid')
    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(status_code=200, text='ok', json=lambda: {
            'code': 0, 'errcode': 0, 'StatusCode': 0, 'ok': True, 'success': True,
            'id': 'fake-id', 'ret': 0, 'status': 'ok', 'access_token': 'fake-token'})
    for method in ['get', 'post', 'request']:
        monkeypatch.setattr(delivery.requests, method, request)
    result = delivery.send('example-title', 'example-body', True, [channel], **config)
    assert result[0]['channel'] == channel
    assert len(calls) == (2 if function == 'wecom_app' else 1)
    args, kwargs = calls[-1]
    wire = repr(args) + repr(kwargs)
    assert 'example-body' in wire
    assert 'example-title' in wire or function == 'ntfy'  # ntfy uses encoded MIME title
    assert kwargs['timeout'] == 15
    if function == 'telegram_bot':
        assert 'fake-TG_BOT_TOKEN' in kwargs['url']
        assert kwargs['data']['chat_id'] == 'fake-TG_USER_ID'
    if function == 'wxpusher_bot':
        assert kwargs['json']['topicIds'] == [123, 456]
        assert kwargs['json']['uids'] == ['fake-uid']


def test_real_smtp_uses_credentials_recipients_and_body(monkeypatch):
    from email import message_from_string
    calls = []
    config = configured()
    config.update(SMTP_SSL='true', SMTP_EMAIL='fake@example.invalid', SMTP_EMAIL_TO='one@example.invalid,two@example.invalid')
    class SMTP:
        def __init__(self, server, **kwargs): calls.append(('connect', server, kwargs))
        def login(self, user, password): calls.append(('login', user, password))
        def sendmail(self, sender, recipients, message): calls.append(('send', sender, recipients, message))
        def quit(self): calls.append(('quit',))
    monkeypatch.setattr(delivery.smtplib, 'SMTP_SSL', SMTP)
    result = delivery.send('example-title', 'example-body', True, ['smtp'], **config)
    assert result[0]['ok'] is True
    assert calls[1] == ('login', 'fake@example.invalid', 'fake-SMTP_PASSWORD')
    assert calls[2][2] == ['one@example.invalid', 'two@example.invalid']
    assert message_from_string(calls[2][3]).get_payload(decode=True).decode() == 'example-body'


def test_task_notifier_filters_before_enqueue_and_reports_capacity_rejection(monkeypatch):
    from app.infrastructure.execution.task_run_notifier import TaskRunNotifier
    monkeypatch.setattr(settings, 'use_notify_queue', True)
    calls = []
    queue = NotifyQueue(maxsize=1, autostart=False, handler=lambda *args: calls.append(args))
    monkeypatch.setattr(sender, 'get_notify_queue', lambda: queue)
    notifier = TaskRunNotifier()
    assert notifier.send('blocked', 'body', meta={'config': {'__exclude_keywords': ['blocked']}}) is False
    assert queue.qsize() == 0
    assert notifier.send('first', 'body', meta={'config': {'BARK_PUSH': 'fake'}}) is True
    assert notifier.send('second', 'body') is False
    queue.start()
    assert queue.join(5)
    assert [item[0] for item in calls] == ['first']
    assert calls[0][3]['BARK_PUSH'] == 'fake'


def test_runtime_keywords_filter_before_dispatch(monkeypatch):
    monkeypatch.setattr(sender, 'get_or_create_notification_setting', lambda db: object())
    monkeypatch.setattr(sender, 'load_notification_config', lambda row: {'__filter_keywords': ['wanted']})
    def forbidden(*args, **kwargs):
        raise AssertionError('filtered notification must not dispatch')
    monkeypatch.setattr(sender, 'send', forbidden)
    monkeypatch.setattr(sender, 'send_async', forbidden)
    assert sender.send_runtime(object(), 'other', 'body') == []
