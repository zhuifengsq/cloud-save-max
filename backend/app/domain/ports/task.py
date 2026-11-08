"""任务执行用例端口：触发方与执行实现之间的唯一契约。

目标架构的稳定接口，不急于套用。当前「运行一个任务」有 6 份直连实现
（HTTP/SSE/预览/调度器/Telegram/死代码，见 docs/architecture-evolution-plan.md §1.2），
S3+ 收敛为 TaskRunUseCase 后，触发方只依赖本协议。
"""
from __future__ import annotations

from typing import Any, Mapping, Protocol, runtime_checkable


@runtime_checkable
class TaskRunUseCasePort(Protocol):
    """任务执行用例：触发方只做「调用 + 结果映射」。"""

    def run(self, task_id: int) -> Mapping[str, Any]:
        """执行任务（含准入/审计/通知编排）；返回至少含 status 的结果。"""

    def preview(self, task_id: int) -> Mapping[str, Any]:
        """只演练不落库（SSE 预览等场景）。"""

    def stop(self, task_id: int) -> bool:
        """请求停止正在执行的任务；返回是否找到可停止的执行。"""
