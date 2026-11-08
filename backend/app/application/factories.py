"""实现工厂（composition root 的第二部分）——**唯一**允许点名 infrastructure 实现的地方。

为什么单独一个模块而不并进 container.py：
container.py 被 services（application）广泛 import，如果它再去 import
extensions.runtime.task_executor，就形成
`container -> task_executor -> services -> container` 的 import 环（S3 已把环清零，
门禁会报 cycles）。工厂只被用例层（application/use_cases）引用，处于依赖图末端，
因此不会把环带回来。

分层约定（见 docs/architecture-evolution-plan.md §3.1 / §10.3 决策 4）：
本模块在 scripts/check_layering.py 里登记为 composition-root，任何层都可以引用它；
业务代码只允许通过它拿"实现类/实现函数"，不允许在这里写业务分支。
"""
from __future__ import annotations


def task_executor_class():
    """任务执行器实现类（TaskExecutor 目前位于 app/extensions/runtime）。"""
    from app.infrastructure.execution.task_executor import TaskExecutor

    return TaskExecutor
