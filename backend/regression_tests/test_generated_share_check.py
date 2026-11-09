import json
from contextlib import nullcontext
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from treelib import Tree

from app.infrastructure.persistence.base import Base
from app.infrastructure.drive.adapters.baidu_adapter import BaiduAdapter
from app.infrastructure.drive.adapters.quark_adapter import QuarkAdapter
from app.infrastructure.execution import task_executor
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.models.task_execution import TaskExecution
from app.infrastructure.persistence.models.system_setting import SystemSetting
from app.infrastructure.persistence.models.task_scheduler_setting import TaskSchedulerSetting
from app.application.dashboard_drama import build_drama_overview
from app.application.generated_share_check import check_generated_share, read_share_check
from app.application.notifications.task_notify import build_task_section
from app.application.task_share_links import save_task_share_link
from app.application.tasks import create_task


def test_share_expiry_notifies_even_when_transfer_has_no_new_files():
    execution = SimpleNamespace(
        status="success", tree_summary=None,
        adapter_snapshot=json.dumps({"generated_share_check": {
            "status": "invalid", "message": "分享链接已失效", "notify": True,
        }}),
    )
    message, should_notify = build_task_section(SimpleNamespace(taskname="嫁金钗"), execution)
    assert should_notify
    assert "嫁金钗" in message and "分享链接已失效" in message


def test_history_failure_is_a_check_error():
    task = SimpleNamespace(id=12, extra_json=None)
    save_task_share_link(task, share_url="https://pan.quark.cn/s/public")

    def unavailable(statement):
        raise RuntimeError("database is locked")

    db = SimpleNamespace(no_autoflush=nullcontext(), scalars=unavailable)
    check = check_generated_share(db, task, None)
    assert check["status"] == "error" and not check["notify"]


def test_baidu_probe_keeps_verify_expiry_code(monkeypatch):
    adapter = object.__new__(BaiduAdapter)
    adapter._session = object()
    monkeypatch.setattr(adapter, "_api_access_shared", lambda url, password: {"errno": 105})
    result = adapter.get_stoken("public", "1234", check_errno=True)
    assert result["code"] == 105 and result["message"] == "分享链接已过期"


def test_checks_generated_link_each_run_without_blocking_transfer(monkeypatch):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[
            Task.__table__,
            TaskExecution.__table__,
            TaskSchedulerSetting.__table__,
            SystemSetting.__table__,  # 仪表盘「近期失败」清理水位
        ],
    )
    events = []
    responses = iter([
        {"code": 41012},
        TimeoutError(),
        {"code": 41012},
        {"code": 0, "data": {"stoken": "verified"}},
        {"code": 41012},
        {"code": 41012},
        {"code": 41012},
    ])

    def token(share_id, passcode):
        events.append("check")
        assert share_id == "public"
        assert passcode == "1234"
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result

    def detail(share_id, stoken, folder, *, first_page):
        assert first_page
        return {"code": 0, "data": {"list": []}}

    # 用真实的 QuarkAdapter 实例（不初始化网络），让检测走真实的失败分类逻辑
    adapter = object.__new__(QuarkAdapter)
    adapter.is_active = True
    adapter.nickname = "测试"
    adapter.ensure_initialized = lambda **kw: True
    adapter.extract_url = lambda url: (url.rsplit("/", 1)[-1], "", "0", [])
    adapter.get_stoken = token
    adapter.get_detail = detail
    manager = SimpleNamespace(init_for_tasks=lambda _: None, get_adapter_for_task=lambda _, **kw: adapter)

    class FakeDrama:
        def __init__(self, *, adapter, task_data, log):
            assert task_data["shareurl"] == "https://pan.quark.cn/s/source"

        def execute(self):
            events.append("transfer")
            tree = Tree()
            tree.create_node("完成", "root")
            return tree

    monkeypatch.setattr(task_executor, "DramaTaskExecutor", FakeDrama)
    monkeypatch.setattr("app.application.magic_regex.get_enabled_magic_regex_map", lambda db: {})
    with Session(engine) as db:
        task = create_task(
            db, task_type="drama", taskname="测试剧",
            shareurl="https://pan.quark.cn/s/source", savepath="/测试",
            extra={"share_link": {"share_url": "https://pan.quark.cn/s/source"}},
        )
        assert check_generated_share(db, task, manager) is None
        save_task_share_link(task, share_url="https://pan.quark.cn/s/public", share_id="public", passcode="1234")
        db.commit()

        for expected_status, expected_notify in [
            ("invalid", True), ("error", False), ("invalid", False), ("valid", False), ("invalid", True),
        ]:
            execution = task_executor.TaskExecutor(db).run_task(task, account_manager=manager)
            db.commit()
            assert execution.status == "success"
            check = read_share_check(execution.adapter_snapshot)
            assert check["status"] == expected_status and check["notify"] == expected_notify
            assert "1234" not in execution.adapter_snapshot
            assert build_task_section(task, execution)[1] == expected_notify

        assert events == ["check", "transfer"] * 5

        # 手动点「执行」：链接已失效时每次都要提醒，不受去重限制
        for _ in range(2):
            execution = task_executor.TaskExecutor(db).run_task(
                task, account_manager=manager, share_check_always_notify=True,
            )
            db.commit()
            check = read_share_check(execution.adapter_snapshot)
            assert check["status"] == "invalid" and check["notify"] is True
            assert build_task_section(task, execution)[1] is True

        assert events == ["check", "transfer"] * 7
        overview = build_drama_overview(db)
        assert overview["summary"]["execution_failed"] == 0
        failures = overview["recent_failures"]
        assert len(failures) == 6 and all(row["stage"] == "generated_share_check" for row in failures)
    engine.dispose()
