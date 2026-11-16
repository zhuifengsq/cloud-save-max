"""Task run orchestration: detached read plan, external engine, short writes, notification.

Production uses execute(command, policy). Admission remains at the entry so HTTP/SSE
reject 409/429 before starting work. The execute entry owns the staged transaction
boundary; removed test-only compatibility code is restored only by reverting F1.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import time
from app.application.dto.task_run import RunTaskCommand, TaskRunResult
from app.domain.task_run import ExecutionLogPort, RunPolicyPort, TaskExecutionState
from app.core.runtime.tx import end_read_tx
from app.core.errors import ApiError
from app.domain.ports.task import TaskRunStorePort, TaskExecutionEnginePort
from app.domain.ports.notify import NotifierPort
from app.application import audit
from app.application.notifications.task_notify import (
    build_task_section,
    build_share_check_section,
    DRAMA_NOTIFY_TITLE,
)

logger = logging.getLogger(__name__)

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class RunPolicy:
    """一次任务执行的行为参数：只描述"触发方差异"，不承载业务参数（业务参数在 task 上）。"""

    trigger: str
    # 生成分享链接检测是否强制通知（不强制时只在异常时通知）
    share_check_always_notify: bool = True
    # 是否写入 TaskExecution 记录（分享预览不落库）
    persist_execution: bool = True
    # 是否「手动触发」：手动执行不受追剧任务「运行星期」限制（用户要求：不到设定日期也能点、也能跑）；
    # 只有 APScheduler 定时轮询为 False。执行器据此跳过日期类拦截，见 app/application/drama_schedule.py。
    manual_run: bool = True

    @classmethod
    def for_trigger(cls, trigger: str) -> "RunPolicy":
        normalized = str(trigger or "").strip().lower()
        policy = _TRIGGER_POLICIES.get(normalized)
        if policy is None:
            raise ValueError(
                f"未知的任务触发方: {trigger!r}（可选: {', '.join(sorted(_TRIGGER_POLICIES))}）"
            )
        return policy


# 触发方真值表（改造前逐点核对，勿随意改动）：
# | trigger   | share_check_always_notify | persist_execution | manual_run | 调用点 | 准入（仍在调用点） |
# | http      | True                      | True              | True       | POST /api/tasks/{id}/run | with task_admission.reserve(task_id) |
# | sse       | True                      | True              | True       | POST /api/tasks/{id}/run/stream | slot = reserve(task_id) 后 start_worker |
# | scheduler | False                     | True              | False      | APScheduler run_drama_tasks | reserve(task_id) + with slot |
# | telegram  | True                      | True              | True       | Telegram bot 手动执行 | handlers._run_async 提前占槽 |
# | preview   | False                     | False             | True       | POST /api/tasks/run/stream | slot = reserve(None) 后 start_worker |
_TRIGGER_POLICIES: dict[str, RunPolicy] = {
    "http": RunPolicy(
        trigger="http",
        share_check_always_notify=True,
        persist_execution=True,
        manual_run=True,
    ),
    "sse": RunPolicy(
        trigger="sse",
        share_check_always_notify=True,
        persist_execution=True,
        manual_run=True,
    ),
    "scheduler": RunPolicy(
        trigger="scheduler",
        share_check_always_notify=False,
        persist_execution=True,
        manual_run=False,
    ),
    "telegram": RunPolicy(
        trigger="telegram",
        share_check_always_notify=True,
        persist_execution=True,
        manual_run=True,
    ),
    "preview": RunPolicy(
        trigger="preview",
        share_check_always_notify=False,
        persist_execution=False,
        manual_run=True,
    ),
}


class RunTaskUseCase:
    """Own run/recovery persistence, audit and post-commit notification decisions."""

    def __init__(
        self,
        db: Session,
        *,
        store: TaskRunStorePort,
        engine: TaskExecutionEnginePort,
        notifier: NotifierPort,
    ) -> None:
        self.db = db
        self.store = store
        self.engine = engine
        self.notifier = notifier

    def _release_read(self) -> None:
        if self.db.new or self.db.dirty or self.db.deleted:
            raise RuntimeError("Execution read phase contains uncommitted writes")
        end_read_tx(self.db)

    def execute(
        self,
        command: RunTaskCommand,
        policy: RunPolicyPort,
        log: ExecutionLogPort | None = None,
    ) -> TaskRunResult:
        plan = self.store.prepare(command)
        self._release_read()
        result = self.engine.execute(
            plan,
            policy,
            log,
            lambda task, execution_log, tried: self._recover(
                task, execution_log, tried, policy
            ),
        )
        try:
            if policy.persist_execution:
                self.store.save(plan, result)
            audit.write_audit_log(
                self.db,
                actor_user_id=command.actor_user_id,
                action="task.run.preview_stream"
                if not policy.persist_execution
                else "task.run",
                target_type="task",
                target_id=str(command.task_id),
                ip=command.ip,
                user_agent=command.user_agent,
                success=result.status == "success",
                detail=result.message,
            )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        if policy.persist_execution:
            if plan.task.task_type == "drama":
                try:
                    section = (
                        build_share_check_section(plan.task, result)
                        if policy.trigger == "telegram"
                        else build_task_section(plan.task, result)[0]
                    )
                    if section:
                        self.notifier.send(
                            DRAMA_NOTIFY_TITLE,
                            section,
                            meta={
                                "task_id": command.task_id,
                                "config": plan.prepared.get("notification_config", {}),
                            },
                        )
                except Exception as exc:
                    logger.warning(
                        "Final notification failed task_id=%s error_type=%s",
                        command.task_id,
                        type(exc).__name__,
                    )
        return result

    def _recover(
        self,
        task: TaskExecutionState,
        log: ExecutionLogPort,
        tried: set[str] | None,
        policy: RunPolicyPort,
    ) -> bool:
        from app.application.drama_share_autoupdate import (
            resolve_drama_shareurl_update,
            is_auto_update_task,
        )
        from app.application.shareurl_update_logs import append_shareurl_update_log

        if not is_auto_update_task(self.db, task):
            self._release_read()
            return False
        self._release_read()
        tried = tried if tried is not None else set()
        for round_number in range(1, 5):
            if round_number > 1:
                time.sleep(2)
            old = task.shareurl
            round_tried = set(tried)
            failure_message = "未找到可用链接"
            try:
                result = resolve_drama_shareurl_update(
                    self.db,
                    task,
                    tried_shareurls=round_tried,
                    release_read=self._release_read,
                    persist=False,
                )
            except ApiError as exc:
                if exc.code not in {
                    "SEARCH_UPSTREAM_FAILED",
                    "SEARCH_UPSTREAM_TIMEOUT",
                }:
                    raise
                result = {"checked": True, "updated": False}
                failure_message = (
                    "搜索连接超时"
                    if exc.code == "SEARCH_UPSTREAM_TIMEOUT"
                    else "搜索来源暂不可用"
                )
            self._release_read()
            updated = bool(result.get("updated"))
            entry = append_shareurl_update_log(
                task,
                source="auto",
                status="success" if updated else "failed",
                message="已自动更换源链接" if updated else failure_message,
                old_shareurl=old,
                new_shareurl=str(result.get("new_shareurl") or ""),
                round_number=round_number,
            )
            log.line(f"自动换链 {round_number}/4: {entry['message']}")
            if policy.persist_execution:
                try:
                    self.store.save_task(task)
                    self.db.commit()
                except BaseException:
                    self.db.rollback()
                    raise
            if updated:
                tried.update(round_tried)
                log.line(f"OK: 自动换链成功 new={task.shareurl}")
                return True
            if not result.get("checked"):
                break
        return False
