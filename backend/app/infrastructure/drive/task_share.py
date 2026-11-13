"""Database planning/persistence and gateway construction for task sharing."""
from app.core.errors import ApiError, bad_request, not_found
from app.domain.ports.task_share import CreatedShare, TaskSharePlan
from app.infrastructure.persistence.repositories import tasks, drive_accounts
from app.infrastructure.drive.adapter_registry import AdapterRegistry
from app.infrastructure.drive.factory import AdapterFactory
from app.infrastructure.drive.gateway import gateway_for_adapter
from app.application.task_share_links import save_task_share_link


class SqlTaskShareStore:
    def __init__(self, db):
        self.db = db

    def prepare(self, task_id: int) -> TaskSharePlan:
        task = tasks.get_task_row(self.db, task_id)
        if task is None:
            raise not_found('TASK_NOT_FOUND', '任务不存在')
        if task.account_name:
            account = drive_accounts.find_active_by_name(self.db, name=task.account_name)
            if account is None:
                raise not_found('DRIVE_ACCOUNT_NOT_FOUND', '任务指定的网盘账号不存在或不可用')
        else:
            drive = AdapterRegistry.detect_drive_type(task.shareurl)
            if not drive:
                raise bad_request('TASK_SHAREURL_INVALID', '无法识别任务使用的网盘类型')
            account = drive_accounts.find_active_by_type(self.db, drive_type=drive)
            if account is None:
                raise not_found('DRIVE_ACCOUNT_NOT_FOUND', '没有可用的网盘账号')
        return TaskSharePlan(
            task_id=task.id, taskname=task.taskname, savepath=task.savepath,
            shareurl=task.shareurl, task_account_name=task.account_name,
            account_name=account.name, drive_type=account.drive_type,
            config=AdapterRegistry.parse_config_json(account.drive_type, account.config_json, account.cookie),
            cookie=account.cookie,
        )

    def save(self, plan: TaskSharePlan, result: CreatedShare) -> str:
        task = tasks.get_task_row(self.db, plan.task_id)
        if task is None:
            raise not_found('TASK_NOT_FOUND', '任务不存在')
        if task.savepath != plan.savepath or task.account_name != plan.task_account_name:
            raise ApiError('TASK_CHANGED', '任务保存路径或账号已变更，请核对生成的分享', 409)
        record = save_task_share_link(
            task, share_url=result.url, share_id=result.share_id,
            title=result.title or plan.taskname, drive_type=plan.drive_type,
            account_name=plan.account_name, passcode=result.passcode or None,
            expires_in_days=result.expires_in_days,
        )
        self.db.flush()
        return str(record['saved_at'])


def open_task_share_gateway(plan: TaskSharePlan):
    # This function runs only after the use case closes its planning transaction.
    adapter = AdapterFactory.create_adapter(
        plan.drive_type, plan.cookie, config=dict(plan.config), account_name=plan.account_name,
    )
    if adapter is None:
        raise bad_request('DRIVE_ACCOUNT_UNAVAILABLE', '网盘账号登录失败，请检查账号状态')
    gateway = gateway_for_adapter(adapter, drive_type=plan.drive_type)
    if not gateway.capabilities.supports_share:
        return gateway
    if not adapter.is_active and not adapter.init():
        raise bad_request('DRIVE_ACCOUNT_UNAVAILABLE', '网盘账号登录失败，请检查账号状态')
    return gateway
