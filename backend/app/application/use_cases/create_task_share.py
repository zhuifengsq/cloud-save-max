"""Create a cloud share, then atomically save its record and audit trail."""
import logging
from typing import Callable

from app.application import audit
from app.application.container import transaction, task_share_store, task_share_gateway
from app.application.dto.task_share import CreateTaskShareCommand, TaskShareResult
from app.core.errors import ApiError, bad_request
from app.core.runtime.tx import end_read_tx
from app.domain.ports.task_share import TaskShareStorePort, TaskSharePlan, TaskShareGatewayPort

logger = logging.getLogger(__name__)


class CreateTaskShareUseCase:
    def __init__(self, db, *, store: TaskShareStorePort | None = None,
                 gateway_factory: Callable[[TaskSharePlan], TaskShareGatewayPort] | None = None):
        self.db = db
        self.store = store if store is not None else task_share_store(db)
        self.gateway_factory = gateway_factory or task_share_gateway

    def execute(self, command: CreateTaskShareCommand) -> TaskShareResult:
        plan = self.store.prepare(command.task_id)
        end_read_tx(self.db)
        gateway = self.gateway_factory(plan)
        if not gateway.capabilities.supports_share:
            raise bad_request('TASK_SHARE_UNSUPPORTED', f'{plan.drive_type}暂不支持生成分享链接')
        try:
            created = gateway.create_share_link(plan.savepath, plan.taskname)
        except ValueError as exc:
            raise bad_request('TASK_SHARE_PATH_INVALID', str(exc)) from exc
        except NotImplementedError as exc:
            raise bad_request('TASK_SHARE_UNSUPPORTED', f'{plan.drive_type}暂不支持生成分享链接') from exc
        except ApiError:
            raise
        except Exception as exc:
            logger.warning('生成分享失败 task_id=%s drive_type=%s error_type=%s',
                           plan.task_id, plan.drive_type, type(exc).__name__)
            raise bad_request('TASK_SHARE_FAILED', '生成分享链接失败，请检查账号状态后重试') from exc
        if not created.supported:
            raise bad_request('TASK_SHARE_UNSUPPORTED', f'{plan.drive_type}暂不支持生成分享链接')
        if not created.url.strip():
            raise bad_request('TASK_SHARE_FAILED', '网盘没有返回分享链接')
        # Never retry the cloud call when this local transaction fails.
        try:
            with transaction(self.db):
                saved_at = self.store.save(plan, created)
                audit.write_audit_log(
                    self.db, actor_user_id=command.actor_user_id, action='task.share.create',
                    target_type='task', target_id=str(plan.task_id), ip=command.ip,
                    user_agent=command.user_agent, success=True, detail=f'drive_type={plan.drive_type}',
                )
        except Exception as exc:
            logger.warning('Share persistence failed task_id=%s drive_type=%s error_type=%s',
                           plan.task_id, plan.drive_type, type(exc).__name__)
            raise
        return TaskShareResult(
            share_url=created.url, share_id=created.share_id, title=created.title or plan.taskname,
            drive_type=plan.drive_type, account_name=plan.account_name, passcode=created.passcode or None,
            expires_in_days=created.expires_in_days, saved_at=saved_at,
        )
