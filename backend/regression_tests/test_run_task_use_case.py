"""RunPolicy 真值表 + 统一执行入口参数对齐（纯函数断言，不连库）。

边界：准入（task_admission.reserve）仍在各调用点，本用例不占槽——这里用假执行器
记录实参，验证灰度开关两端的参数与改造前逐点一致。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from app.application.use_cases import run_task as run_task_use_case
from app.application.use_cases.run_task import RunPolicy, RunTaskUseCase


# trigger -> (share_check_always_notify, persist_execution, manual_run)
# manual_run=True 表示「手动触发」：追剧任务不受运行星期限制；只有 scheduler 定时轮询为 False。
TRUTH_TABLE = {
    "http": (True, True, True),
    "sse": (True, True, True),
    "scheduler": (False, True, False),
    "telegram": (True, True, True),
    "preview": (False, False, True),
}


@pytest.mark.parametrize("trigger", sorted(TRUTH_TABLE))
def test_policy_truth_table(trigger):
    policy = RunPolicy.for_trigger(trigger)
    assert policy.trigger == trigger
    assert (
        policy.share_check_always_notify,
        policy.persist_execution,
        policy.manual_run,
    ) == TRUTH_TABLE[trigger]


def test_for_trigger_normalizes_case_and_space():
    assert RunPolicy.for_trigger("  SSE ") == RunPolicy.for_trigger("sse")


def test_for_trigger_rejects_unknown():
    with pytest.raises(ValueError):
        RunPolicy.for_trigger("unknown")
    with pytest.raises(ValueError):
        RunPolicy.for_trigger("")


def test_policy_is_frozen():
    policy = RunPolicy.for_trigger("http")
    with pytest.raises(FrozenInstanceError):
        policy.persist_execution = False  # type: ignore[misc]


def test_policy_has_no_slot_flag():
    """准入归调用点，RunPolicy 不得再承载 use_slot（避免死字段误导）。"""
    assert not hasattr(RunPolicy.for_trigger("http"), "use_slot")


class _RecordingExecutor:
    calls: list[dict] = []

    def __init__(self, db):
        self.db = db

    def run_task(self, task, **kwargs):
        _RecordingExecutor.calls.append({"task": task, "kwargs": kwargs})
        return SimpleNamespace(status="success")


@pytest.fixture
def fake_deps(monkeypatch):
    _RecordingExecutor.calls = []

    def fake_executor_cls(db):
        return _RecordingExecutor(db)

    task = SimpleNamespace(id=17)
    return SimpleNamespace(task=task, executor_cls=fake_executor_cls)


def _set_flag(monkeypatch, value: bool) -> None:
    monkeypatch.setattr(
        run_task_use_case,
        "settings",
        SimpleNamespace(use_unified_run_use_case=value),
    )


def test_legacy_path_matches_old_call_arguments(fake_deps, monkeypatch):
    """灰度开关关闭时，逐字复刻改造前 5 个活调用点的实参（第 6 处是已删除的死代码）。"""
    _set_flag(monkeypatch, False)
    log = object()
    manager = object()

    for trigger in ("http", "telegram"):
        RunTaskUseCase(db=object(), executor_cls=fake_deps.executor_cls).run(
            fake_deps.task, RunPolicy.for_trigger(trigger)
        )
    RunTaskUseCase(db=object(), executor_cls=fake_deps.executor_cls).run(
        fake_deps.task, RunPolicy.for_trigger("sse"), log=log
    )
    RunTaskUseCase(db=object(), executor_cls=fake_deps.executor_cls).run(
        fake_deps.task,
        RunPolicy.for_trigger("scheduler"),
        account_manager=manager,
        init_account_for_task=False,
    )
    RunTaskUseCase(db=object(), executor_cls=fake_deps.executor_cls).run(
        fake_deps.task, RunPolicy.for_trigger("preview"), log=log
    )

    assert [call["kwargs"] for call in _RecordingExecutor.calls] == [
        {"share_check_always_notify": True, "manual_run": True},
        {"share_check_always_notify": True, "manual_run": True},
        {"log": log, "share_check_always_notify": True, "manual_run": True},
        {"account_manager": manager, "init_account_for_task": False, "manual_run": False},
        {"log": log, "persist_execution": False, "manual_run": True},
    ]


def test_unified_path_derives_arguments_from_policy(fake_deps, monkeypatch):
    _set_flag(monkeypatch, True)
    manager = object()

    RunTaskUseCase(db=object(), executor_cls=fake_deps.executor_cls).run(
        fake_deps.task,
        RunPolicy.for_trigger("scheduler"),
        account_manager=manager,
        init_account_for_task=False,
    )

    assert _RecordingExecutor.calls[0]["kwargs"] == {
        "log": None,
        "persist_execution": True,
        "account_manager": manager,
        "init_account_for_task": False,
        "share_check_always_notify": False,
        "manual_run": False,
    }


def test_default_execution_dependency_is_an_assembled_port_instance():
    from app.infrastructure.execution.task_run_stages import PreparedTaskEngine
    use_case = RunTaskUseCase(db=object())
    assert isinstance(use_case.engine, PreparedTaskEngine)
    assert use_case.executor_cls is None
