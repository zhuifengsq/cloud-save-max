from app.application.search_results import SearchBatch
import json
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from treelib import Tree

from app.infrastructure.persistence.base import Base
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.models.task_execution import TaskExecution
from app.infrastructure.persistence.models.drive_account import DriveAccount
from app.application.use_cases.run_task import RunTaskUseCase, RunPolicy
from app.application.dto.task_run import RunTaskCommand
from app.core.errors import ApiError
from app.application.tasks import update_task
from app.application.task_share_links import save_task_share_link, get_task_share_link
from app.application.shareurl_update_logs import append_shareurl_update_log
from app.application.resource_search import fetch_task_suggestions, cache_clear
from app.application.drama_share_autoupdate import _search_candidates


class ShareRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://')
        import app.infrastructure.persistence.models
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.task = Task(task_uid='recovery', task_type='drama', taskname='测试剧',
                         shareurl='https://pan.quark.cn/s/old', savepath='/测试剧', enabled=True,
                         extra_json=json.dumps({'auto_update_shareurl': True}))
        self.db.add(self.task)
        self.db.flush()
        self.original = save_task_share_link(self.task, share_url='https://pan.quark.cn/s/public', passcode='1234')
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    def logs(self):
        return json.loads(self.task.extra_json).get('shareurl_update_logs', [])

    def run_recovery(self, results, *, always_fail=False, preview_results=None):
        calls = []
        adapter = SimpleNamespace(is_active=True, nickname='测试', DRIVE_TYPE='quark')
        manager = SimpleNamespace(init_for_tasks=lambda _: None, get_adapter_for_task=lambda _: adapter)

        def search(*args, **kwargs):
            calls.append(kwargs)
            outcome = results[min(len(calls) - 1, len(results) - 1)]
            if isinstance(outcome, Exception):
                raise outcome
            return (outcome, False, {})

        class FakeDrama:
            def __init__(self, *, adapter, task_data, log):
                self.data, self.log = task_data, log

            def execute(self):
                if always_fail or self.data['shareurl'].endswith('/old'):
                    self.log.set_stage('get_stoken')
                    raise RuntimeError('分享已失效')
                tree = Tree()
                tree.create_node('完成', 'root')
                return tree

        with ExitStack() as stack:
            stack.enter_context(patch('app.infrastructure.execution.task_executor.DramaTaskExecutor', FakeDrama))
            stack.enter_context(patch('app.application.magic_regex.get_enabled_magic_regex_map', return_value={}))
            stack.enter_context(patch('app.application.tmdb_settings.get_or_create_tmdb_setting', return_value=None))
            stack.enter_context(patch('app.application.tmdb_settings.get_tmdb_runtime_config', return_value={}))
            stack.enter_context(patch('app.application.drama_share_autoupdate._search_candidates', side_effect=search))
            stack.enter_context(patch('app.application.drama_share_autoupdate._prepare_candidates', side_effect=lambda db, suggestions, addition, **kwargs: (suggestions, {})))
            stack.enter_context(patch('app.application.share_preview_batch.fetch_share_file_list_grouped',
                                      side_effect=preview_results,
                                      return_value=[([{'file_name': '01.mp4'}], 'folder', 0, '测试剧')]))
            stack.enter_context(patch('time.sleep'))
            stack.enter_context(patch('app.infrastructure.execution.task_run_stages.DatabaseAccountManager.from_payload', return_value=manager))
            # Setup edits are committed before invoking the production application boundary.
            task_id = self.task.id
            self.db.commit()
            result = RunTaskUseCase(self.db).execute(RunTaskCommand(task_id), RunPolicy.for_trigger('http'))
        return result, calls

    def test_empty_search_runs_four_rounds_and_persists_failure_logs(self):
        execution, calls = self.run_recovery([[]])
        self.assertEqual(len(calls), 4)
        self.assertEqual(execution.status, 'failed')
        self.db.expire_all()
        self.assertEqual([row['round'] for row in self.logs()], [1, 2, 3, 4])
        self.assertTrue(all(row['status'] == 'failed' for row in self.logs()))
        self.assertEqual(get_task_share_link(self.task), self.original)
        self.assertEqual(len(self.db.scalars(select(TaskExecution)).all()), 1)

    def test_second_round_success_preserves_public_share_and_full_execution_log(self):
        execution, calls = self.run_recovery([[], [{'shareurl': 'https://pan.quark.cn/s/new'}]])
        self.assertEqual(execution.status, 'success')
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.task.shareurl, 'https://pan.quark.cn/s/new#/list/share/folder')
        self.assertEqual(get_task_share_link(self.task), self.original)
        self.assertEqual([row['status'] for row in self.logs()], ['failed', 'success'])
        self.assertIn('自动换链', execution.run_log)
        self.assertIn('1/4', execution.run_log)
        self.assertIn('2/4', execution.run_log)
        self.assertEqual(len(self.db.scalars(select(TaskExecution)).all()), 1)

    def test_exception_is_logged_and_later_round_can_recover(self):
        execution, calls = self.run_recovery([ApiError('SEARCH_UPSTREAM_TIMEOUT', '搜索连接超时', 504), [{'shareurl': 'https://pan.quark.cn/s/new'}]])
        self.assertEqual(execution.status, 'success')
        self.assertEqual(len(calls), 2)
        self.assertIn('搜索连接超时', self.logs()[0]['message'])

    def test_disabled_switch_never_searches(self):
        self.task.extra_json = json.dumps({'auto_update_shareurl': False, 'share_link': self.original})
        execution, calls = self.run_recovery([[]])
        self.assertEqual(execution.status, 'failed')
        self.assertEqual(calls, [])
        self.assertEqual(self.logs(), [])

    def test_manual_source_change_preserves_public_share_and_logs_once(self):
        update_task(self.db, self.task.id, shareurl='https://pan.quark.cn/s/manual')
        self.db.commit()
        self.assertEqual(get_task_share_link(self.task), self.original)
        self.assertEqual(len(self.logs()), 1)
        self.assertEqual(self.logs()[0]['source'], 'manual')
        self.assertEqual(self.logs()[0]['old_shareurl'], 'https://pan.quark.cn/s/old')
        update_task(self.db, self.task.id, shareurl='https://pan.quark.cn/s/manual')
        self.assertEqual(len(self.logs()), 1)

    def test_stale_form_cannot_erase_server_logs_or_public_share(self):
        update_task(self.db, self.task.id, shareurl='https://pan.quark.cn/s/first')
        update_task(self.db, self.task.id, shareurl='https://pan.quark.cn/s/second',
                    extra={'shareurl_update_logs': [], 'share_link': None, 'auto_update_shareurl': False})
        self.assertEqual(get_task_share_link(self.task), self.original)
        self.assertEqual(len(self.logs()), 2)
        self.assertFalse(json.loads(self.task.extra_json)['auto_update_shareurl'])

    def test_destination_change_still_invalidates_outbound_share(self):
        update_task(self.db, self.task.id, savepath='/其他目录')
        self.assertIsNone(get_task_share_link(self.task))

    def test_first_round_success_does_not_search_again(self):
        execution, calls = self.run_recovery([[{'shareurl': 'https://pan.quark.cn/s/new'}]])
        self.assertEqual(execution.status, 'success')
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.logs()), 1)
        self.assertEqual(get_task_share_link(self.task), self.original)

    def test_fourth_round_can_recover(self):
        execution, calls = self.run_recovery([[], [], [], [{'shareurl': 'https://pan.quark.cn/s/new'}]])
        self.assertEqual(execution.status, 'success')
        self.assertEqual(len(calls), 4)
        self.assertEqual([row['status'] for row in self.logs()], ['failed', 'failed', 'failed', 'success'])

    def test_unusable_new_source_stops_at_existing_replacement_limit(self):
        candidates = [[{'shareurl': f'https://pan.quark.cn/s/new{i}'}] for i in range(7)]
        execution, calls = self.run_recovery(candidates, always_fail=True)
        self.assertEqual(execution.status, 'failed')
        self.assertEqual(len(calls), 5)
        self.assertEqual(len(self.logs()), 5)
        self.assertEqual(get_task_share_link(self.task), self.original)
        self.assertIn('new4', self.task.shareurl)
        self.assertIn('new0', execution.run_log)

    def test_probe_failure_can_be_retried_in_next_search_round(self):
        execution, calls = self.run_recovery(
            [[{'shareurl': 'https://pan.quark.cn/s/new'}]],
            preview_results=[[], [([{'file_name': '01.mp4'}], 'folder', 0, '测试剧')]],
        )
        self.assertEqual(execution.status, 'success')
        self.assertEqual(len(calls), 2)
        self.assertEqual([row['status'] for row in self.logs()], ['failed', 'success'])

    def test_logs_are_bounded_without_erasing_existing_extra(self):
        for i in range(55):
            append_shareurl_update_log(self.task, source='auto', status='failed', message=str(i))
        self.assertEqual(len(self.logs()), 50)
        self.assertEqual(self.logs()[0]['message'], '5')
        self.assertEqual(self.logs()[-1]['message'], '54')
        self.assertEqual(get_task_share_link(self.task), self.original)

    def test_recovery_search_bypasses_empty_cache_and_queries_sources_again(self):
        DriveAccount.__table__.create(self.engine, checkfirst=True)
        self.db.add(DriveAccount(name='test', drive_type='quark', cookie='', enabled=True, runtime_status='active'))
        self.db.flush()
        cache_clear()
        with (
            patch('app.application.resource_search._update_search_cache_ttl'),
            patch('app.application.resource_search.list_invalid_shareurls', return_value=[]),
            patch('app.application.search_plugins.search_enabled_plugins_result', return_value=SearchBatch()),
            patch('app.application.api_search_sources.search_enabled_api_sources_result', side_effect=[SearchBatch(sources=1,succeeded=1), SearchBatch(items=[{'shareurl': 'https://pan.quark.cn/s/fresh'}],sources=1,succeeded=1)]) as source,
        ):
            self.assertEqual(fetch_task_suggestions(self.db, '测试剧', 1, drive_type='quark')[0], [])
            self.assertEqual(fetch_task_suggestions(self.db, '测试剧', 1, drive_type='quark')[0], [])
            self.assertEqual(source.call_count, 1)
            results, _, _ = _search_candidates(self.db, names=['测试剧'], drive_type='quark')
            self.assertEqual(source.call_count, 2)
        cache_clear()
        self.assertEqual([item['shareurl'] for item in results], ['https://pan.quark.cn/s/fresh'])


if __name__ == '__main__':
    unittest.main()
