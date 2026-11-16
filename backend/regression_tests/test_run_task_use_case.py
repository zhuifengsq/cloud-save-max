"""RunPolicy 真值表与现役执行端口装配。

边界：准入（task_admission.reserve）仍在各调用点，本用例不占槽——这里验证触发策略，execute 行为在 test_task_run_phases 中覆盖。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from app.application.use_cases.run_task import RunPolicy
from app.application.container import run_task_use_case as RunTaskUseCase


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


def test_default_execution_dependency_is_an_assembled_port_instance():
    from app.infrastructure.execution.task_run_stages import PreparedTaskEngine
    use_case = RunTaskUseCase(db=object())
    assert isinstance(use_case.engine, PreparedTaskEngine)
