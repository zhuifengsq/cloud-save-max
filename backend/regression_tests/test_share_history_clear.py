import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from starlette.requests import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes import dashboard
from app.db.base import Base
from app.models.task import Task
from app.models.task_execution import TaskExecution


class ClearShareHistoryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine, tables=[Task.__table__, TaskExecution.__table__])
        self.db = Session(self.engine)
        self.extra = {'auto_update_shareurl': True, 'share_link': {'share_url': 'https://pan.quark.cn/s/public'},
                      'saved_latest': {'episode': 12}, 'shareurl_update_logs': [{'id': 'a'}, {'id': 'b'}]}
        for i, kind in enumerate(['drama', 'generic'], 1):
            self.db.add(Task(id=i, task_uid=str(i), task_type=kind, taskname='test',
                             shareurl='https://pan.quark.cn/s/source', savepath='/test',
                             extra_json=json.dumps(self.extra)))
        self.db.add(TaskExecution(task_id=1, status='failed', started_at=datetime.now(), run_log='original'))
        self.db.commit()

    def clear(self):
        handler = getattr(dashboard, 'clear_drama_shareurl_logs', None)
        self.assertTrue(callable(handler), '清除换链日志接口尚未实现')
        request = Request({'type': 'http', 'method': 'POST', 'path': '/dashboard/drama/clear-shareurl-logs', 'headers': []})
        with patch('app.api.routes.dashboard.audit.write_audit_log'):
            return handler(request=request, current=SimpleNamespace(user=SimpleNamespace(id=1)), db=self.db)

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def test_clear_persists_and_preserves_links_settings_and_executions(self):
        self.assertEqual(self.clear(), {'deleted': 2})
        with Session(self.engine) as db:
            task = db.get(Task, 1)
            self.assertEqual(json.loads(task.extra_json), {k: v for k, v in self.extra.items() if k != 'shareurl_update_logs'})
            self.assertEqual(task.shareurl, 'https://pan.quark.cn/s/source')
            self.assertEqual(json.loads(db.get(Task, 2).extra_json), self.extra)
            self.assertEqual(db.query(TaskExecution).one().run_log, 'original')

    def test_clear_again_is_noop(self):
        self.clear()
        self.assertEqual(self.clear(), {'deleted': 0})


if __name__ == '__main__':
    unittest.main()
