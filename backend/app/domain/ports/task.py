"""任务执行用例端口：触发方与执行实现之间的唯一契约。

当前唯一实现是 app.application.use_cases.run_task.RunTaskUseCase（内部装配
TaskExecutor）。触发方（HTTP / SSE / 调度器 / Telegram）只依赖本协议，
不依赖 TaskExecutor 的构造参数。

注意：准入（TaskAdmission.reserve）与 SSE 输出协议属于触发方/入口职责，
不在本端口内——见 docs/architecture-evolution-plan.md §10.3 决策 1。
"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TaskRunUseCasePort(Protocol):
    """任务执行用例：接收已加载的任务行与运行策略，返回执行结果。"""

    def run(
        self,
        task: Any,
        policy: Any,
        log: Any = None,
        *,
        account_manager: Any = None,
        init_account_for_task: bool = True,
    ) -> Any:
        """执行任务；返回执行结果（含 status，供触发方映射响应）。"""
