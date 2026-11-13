from app.application.notifications import legacy_notify
from app.application.notifications.sender import _should_send
from app.core.settings import settings

class TaskRunNotifier:
    def __init__(self, db=None): pass
    def send(self, title, message, *, level='info', meta=None):
        config = dict(legacy_notify.DEFAULT_PUSH_CONFIG)
        config.update((meta or {}).get('config') or {})
        if not _should_send(config, title, message):
            return False
        fn = legacy_notify.send_async if settings.use_notify_queue else legacy_notify.send
        fn(title, message, ignore_default_config=True, **config)
        return True
