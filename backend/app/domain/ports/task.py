"""任务执行用例端口：触发方与执行实现之间的唯一契约。

当前唯一实现是 app.application.use_cases.run_task.RunTaskUseCase（内部装配
TaskExecutor）。触发方（HTTP / SSE / 调度器 / Telegram）只依赖本协议，
不依赖 TaskExecutor 的构造参数。

注意：准入（TaskAdmission.reserve）与 SSE 输出协议属于触发方/入口职责，
不在本端口内——见 docs/architecture-evolution-plan.md §10.3 决策 1。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.task_run import (ExecutionLogPort, RecoveryCallback, RunPolicyPort,
    RunTaskCommand, TaskExecutionState, TaskRunPlan, TaskRunResult)


@runtime_checkable
class TaskRunUseCasePort(Protocol):
    """Command/value-based application entry; callers never pass an ORM task row."""

    def execute(self, command: RunTaskCommand, policy: RunPolicyPort, log: ExecutionLogPort | None = None) -> TaskRunResult:
        """Run staged execution and return an application result value."""


class TaskRunStorePort(Protocol):
    def prepare(self, command: RunTaskCommand) -> TaskRunPlan: ...
    def save(self, plan: TaskRunPlan, result: TaskRunResult) -> None: ...
    def save_task(self, task: TaskExecutionState) -> None: ...


class TaskExecutionEnginePort(Protocol):
    def execute(self, plan: TaskRunPlan, policy: RunPolicyPort, log: ExecutionLogPort | None, recover: RecoveryCallback) -> TaskRunResult: ...
