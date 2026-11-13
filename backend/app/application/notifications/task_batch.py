"""Collect committed task notifications; flush once after a scheduler batch."""
import logging
from app.domain.ports.notify import NotifierPort

logger = logging.getLogger(__name__)

class TaskNotificationBatch:
    def __init__(self, notifier: NotifierPort):
        self.notifier = notifier
        self.messages = []

    def send(self, title, message, *, level='info', meta=None):
        self.messages.append((title, message, level, meta))
        return True

    def flush(self):
        pending, self.messages = self.messages, []
        if not pending:
            return
        title, _, level, meta = pending[-1]
        try:
            self.notifier.send(title, '\n\n'.join(item[1] for item in pending), level=level, meta=meta)
        except Exception as exc:
            logger.warning('Scheduled task notification failed count=%s error_type=%s', len(pending), type(exc).__name__)
