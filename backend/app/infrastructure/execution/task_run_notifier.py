from app.application.notifications import sender
from app.application.notifications.sender import _should_send

class TaskRunNotifier:
    def __init__(self, db=None): pass
    def send(self, title, message, *, level='info', meta=None):
        config = dict(sender.DEFAULT_PUSH_CONFIG)
        config.update((meta or {}).get('config') or {})
        if not _should_send(config, title, message):
            return False
        return sender.send_async(title, message, ignore_default_config=True, **config)
