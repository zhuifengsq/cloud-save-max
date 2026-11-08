"""统一任务执行入口（application 用例层）。

背景：改造前 TaskExecutor 直连有 6 处——HTTP 路由、SSE worker 线程、
APScheduler 追剧轮询、Telegram bot、分享预览、以及 services/tasks.py 里的死代码。
每处各自决定"是否强制分享检测通知 / 是否落 TaskExecution 记录 / 是否传 ExecutionLog"，
任何参数调整都要改多个地方。

本用例把这些"触发方差异"收敛为 RunPolicy，并保证行为与改造前逐参数一致；
开关 settings.use_unified_run_use_case=False 时走 _legacy_execute()，逐字复刻旧参数，
便于灰度回退（见 docs/architecture-evolution-plan.md 的 S1）。

边界：执行槽（task_admission.reserve）**仍留在各调用点**，不在本用例内占用——
SSE 端点的 409/429 同步响应是既有 API 契约，不能因为收敛入口而改变；准入的统一
抽到用例层属于后续 S2+（需先定义准入的端口），本次不涉及。

分层说明：application -> extensions 是计划内的过渡债（TaskExecutor 目前仍在
extensions.runtime）。不在本次调整目录，等运行层抽出 domain/ports 后再收口。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.settings import settings
from app.extensions.runtime.execution_log import ExecutionLog
from app.extensions.runtime.task_executor import TaskExecutor
from app.models.task import Task


@dataclass(frozen=True)
class RunPolicy:
    """一次任务执行的行为参数：只描述"触发方差异"，不承载业务参数（业务参数在 task 上）。

    executor_cls 未包含在此：它是测试注入缝，不属于触发方语义。
    """

    trigger: str
    # 生成分享链接检测是否强制通知（不强制时只在异常时通知）
    share_check_always_notify: bool = True
    # 是否写入 TaskExecution 记录（分享预览不落库）
    persist_execution: bool = True

    @classmethod
    def for_trigger(cls, trigger: str) -> "RunPolicy":
        normalized = str(trigger or "").strip().lower()
        policy = _TRIGGER_POLICIES.get(normalized)
        if policy is None:
            raise ValueError(f"未知的任务触发方: {trigger!r}（可选: {', '.join(sorted(_TRIGGER_POLICIES))}）")
        return policy


# 触发方真值表（改造前逐点核对，勿随意改动）：
# | trigger   | share_check_always_notify | persist_execution | 调用点 | 准入（仍在调用点） |
# | http      | True                      | True              | POST /api/tasks/{id}/run | with task_admission.reserve(task_id) |
# | sse       | True                      | True              | POST /api/tasks/{id}/run/stream | slot = reserve(task_id) 后 start_worker |
# | scheduler | False                     | True              | APScheduler run_drama_tasks | reserve(task_id) + with slot |
# | telegram  | True                      | True              | Telegram bot 手动执行 | handlers._run_async 提前占槽 |
# | preview   | False                     | False             | POST /api/tasks/run/stream | slot = reserve(None) 后 start_worker |
_TRIGGER_POLICIES: dict[str, RunPolicy] = {
    "http": RunPolicy(trigger="http", share_check_always_notify=True, persist_execution=True),
    "sse": RunPolicy(trigger="sse", share_check_always_notify=True, persist_execution=True),
    "scheduler": RunPolicy(trigger="scheduler", share_check_always_notify=False, persist_execution=True),
    "telegram": RunPolicy(trigger="telegram", share_check_always_notify=True, persist_execution=True),
    "preview": RunPolicy(trigger="preview", share_check_always_notify=False, persist_execution=False),
}


class RunTaskUseCase:
    """唯一的任务执行入口：调 TaskExecutor -> 返回 TaskExecution。

    事务仍然由调用方负责（HTTP 路由在用例返回后 commit + refresh；SSE worker 自行 commit；
    调度器按重试语义 commit/rollback），本用例只 flush，不 commit。
    准入（执行槽）仍由调用点负责，见模块 docstring。
    """

    def __init__(self, db: Session, *, executor_cls=None):
        self.db = db
        # 测试注入缝：既有回归测试通过 monkeypatch 调用点模块的 TaskExecutor 注入假执行器，
        # 调用点因此把 TaskExecutor 作为类对象传入；生产路径不传，默认用真实实现。
        self.executor_cls = executor_cls or TaskExecutor

    def run(
        self,
        task: Task,
        policy: RunPolicy,
        log: ExecutionLog | None = None,
        *,
        account_manager=None,
        init_account_for_task: bool = True,
    ):
        # account_manager / init_account_for_task 仅调度器使用：它在轮询前已预初始化账号池，
        # 避免每个任务重复登录网盘。其余触发方保持 TaskExecutor 的默认行为。
        if not settings.use_unified_run_use_case:
            return self._legacy_execute(
                task,
                policy,
                log=log,
                account_manager=account_manager,
                init_account_for_task=init_account_for_task,
            )
        executor = self.executor_cls(self.db)
        return executor.run_task(
            task,
            log=log,
            persist_execution=policy.persist_execution,
            account_manager=account_manager,
            init_account_for_task=init_account_for_task,
            share_check_always_notify=policy.share_check_always_notify,
        )

    def _legacy_execute(self, task, policy, *, log, account_manager, init_account_for_task):
        """灰度开关为 False 时的回退路径：逐字复刻改造前 6 个调用点的实参。"""
        executor = self.executor_cls(self.db)
        trigger = policy.trigger
        if trigger in {"http", "telegram"}:
            # 旧：routes/tasks.py:220、telegram_bot/actions.py:480
            return executor.run_task(task, share_check_always_notify=True)
        if trigger == "sse":
            # 旧：routes/tasks_stream.py:175
            return executor.run_task(task, log=log, share_check_always_notify=True)
        if trigger == "scheduler":
            # 旧：extensions/runtime/task_scheduler.py:250-254
            return executor.run_task(task, account_manager=account_manager, init_account_for_task=False)
        if trigger == "preview":
            # 旧：routes/tasks_stream.py:292
            return executor.run_task(task, log=log, persist_execution=False)
        raise ValueError(f"未知的任务触发方: {trigger!r}")
