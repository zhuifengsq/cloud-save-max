"""Guard the actual business entrypoints, not only allowed import directions."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'app'


def test_target_routes_do_not_own_cloud_transactions_or_notifications():
    targets = {
        'api/routes/tasks.py': {'post_run_task', 'post_create_task_share'},
        'api/routes/tasks_stream.py': {'post_run_task_stream', 'post_run_task_stream_by_payload'},
        'api/routes/task_lead_in.py': {'preview_task_lead_in', 'post_task_lead_in_confirm'},
    }
    forbidden = {'TaskExecutor', 'DatabaseAccountManager', 'AdapterFactory', 'commit',
                 'transaction', 'send_runtime', 'build_task_section'}
    for file, functions in targets.items():
        tree = ast.parse((ROOT / file).read_text(encoding='utf-8'))
        seen = set()
        for function in tree.body:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) or function.name not in functions:
                continue
            seen.add(function.name)
            calls = {node.func.id if isinstance(node.func, ast.Name) else node.func.attr
                     for node in ast.walk(function) if isinstance(node, ast.Call)
                     and isinstance(node.func, (ast.Name, ast.Attribute))}
            assert not calls & forbidden, (file, function.name, calls & forbidden)
        assert seen == functions, (file, functions - seen)


def test_drama_executor_does_not_branch_on_drive_write_protocol():
    source = (ROOT / 'infrastructure/execution/drama_executor.py').read_text(encoding='utf-8')
    assert 'inspect.signature' not in source
    assert 'except TypeError' not in source
    assert 'save_as_select_top_fids' not in source
    assert 'move_files_to_target' not in source


def test_telegram_facade_has_no_business_branches_or_inherited_implementation():
    tree = ast.parse((ROOT / 'application/telegram_bot/handlers.py').read_text(encoding='utf-8'))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    assert not cls.bases
    assert {node.name for node in cls.body if isinstance(node, ast.FunctionDef)} == {'__post_init__', 'handle_update'}
