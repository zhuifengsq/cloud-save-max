"""任务管理端点：任务 CRUD、执行、调度设置、模板与建议。

拆分说明：SSE 流式端点在 app.api.routes.tasks_stream，分享预览/验证在 tasks_share，
网盘浏览/建目录/删除在 tasks_drive；响应装配在 api/presenters（tmdb_view /
drive_payload / drive_accounts）。本模块通过 include_router 组合子路由，URL 与响应契约不变。
"""

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.application.use_cases.run_task import RunPolicy, RunTaskUseCase
from app.api.deps import CurrentUser, get_current_user, require_permissions
from app.core.errors import bad_request, not_found
from app.core.permissions import TASK_READ, TASK_RUN, TASK_WRITE
from app.application.container import commit, get_db, system_settings_repo, tasks_repo, transaction
from app.application.container import adapter_factory as _acc_AdapterFactory; AdapterFactory = _acc_AdapterFactory()
from app.application.container import base_adapter_cls as _acc_BaseCloudDriveAdapter; BaseCloudDriveAdapter = _acc_BaseCloudDriveAdapter()
from app.application.container import adapter_registry as _acc_AdapterRegistry; AdapterRegistry = _acc_AdapterRegistry()
from app.application.container import account_manager_cls as _acc_DatabaseAccountManager; DatabaseAccountManager = _acc_DatabaseAccountManager()
from app.application.container import task_scheduler as _acc_task_scheduler_manager; task_scheduler_manager = _acc_task_scheduler_manager()
from app.application.container import task_admission_service as _acc_task_admission; task_admission = _acc_task_admission()
from app.application.container import task_executor_cls as _acc_TaskExecutor; TaskExecutor = _acc_TaskExecutor()
from app.schemas.task_magic_regex import MagicRegexOut, MagicRegexRuleOut
from app.schemas.task_scheduler import TaskSchedulerSettingOut, TaskSchedulerSettingUpdateIn
from app.schemas.task import (
    StopCompletedDramaTasksOut,
    TaskCreateIn,
    TaskExecutionOut,
    TaskOut,
    TaskShareLinkOut,
    TaskStatusIn,
    TaskUpdateIn,
)
from app.schemas.resource_search import TaskSuggestionListOut
from app.application import audit
from app.application.notifications.sender import send_runtime
from app.application.notifications.task_notify import DRAMA_NOTIFY_TITLE, build_task_section
from app.application.task_scheduler import get_or_create_task_scheduler_setting, load_task_scheduler_setting, update_task_scheduler_setting
from app.application.resource_search import fetch_task_suggestions
from app.application.tasks import create_task, delete_task, get_task, list_recent_executions, list_tasks_recent_executions, set_task_enabled, update_task
from app.application.task_share_links import save_task_share_link
from app.application.tmdb_settings import get_tmdb_lang_pair

from app.api.routes import tasks_drive, tasks_share, tasks_stream
from app.application.drama_lead_in import load_lead_in_summary_map
from app.application.drive_accounts import get_active_account, pick_default_account_name
from app.api.presenters.task import execution_out, scheduler_out, task_out
from app.application.tmdb_view import load_tmdb_payload_map, load_tmdb_status_map

router = APIRouter()
logger = logging.getLogger(__name__)



@router.get('/magic-regex', response_model=MagicRegexOut, dependencies=[Depends(require_permissions(TASK_READ))])
def list_magic_regex(db: Session = Depends(get_db)) -> MagicRegexOut:
    from app.application.magic_regex import list_enabled_effective_rules_for_picker

    rules = [
        MagicRegexRuleOut(key=item["key"], label=item.get("label"), pattern=item.get("pattern") or "", replace=item.get("replace") or "")
        for item in list_enabled_effective_rules_for_picker(db)
    ]
    return MagicRegexOut(rules=rules)


@router.get("/suggestions", response_model=TaskSuggestionListOut, dependencies=[Depends(require_permissions(TASK_READ))])
def get_task_suggestions(q: str = "", d: int = 0, drive_type: str = "", sf: str = "", se: str = "", df: str = "", sfm: str = "", sem: str = "", show_blocked: bool = False, db: Session = Depends(get_db)) -> TaskSuggestionListOut:
    try:
        dt = str(drive_type or "").strip() or None
        items, changed, msg = fetch_task_suggestions(db, keyword=q, deep=d, drive_type=dt, search_filter=sf, search_exclude=se, search_date_from=df, search_filter_mode=sfm, search_exclude_mode=sem)
        if changed:
            commit(db)
        # 标记屏蔽分享者（不过滤，只标记）
        if items:
            blocked_value = system_settings_repo().get_value(db, "blocked_sharers", "")
            blocked_set = {name.strip() for name in blocked_value.split("|") if name.strip()}
            if blocked_set:
                for item in items:
                    author = str(item.get("share_author_name") or "").strip()
                    if author and author in blocked_set:
                        item["is_blocked_sharer"] = True
                    else:
                        item["is_blocked_sharer"] = False
            else:
                for item in items:
                    item["is_blocked_sharer"] = False
        return TaskSuggestionListOut(success=True, data=items, message=msg)
    except Exception as e:
        return TaskSuggestionListOut(success=True, message=f"error: {str(e)}", data=[])




@router.get('', response_model=list[TaskOut], dependencies=[Depends(require_permissions(TASK_READ))])
def get_tasks(db: Session = Depends(get_db)):
    items = list_tasks_recent_executions(db, limit=3)
    tmdb_status_map = load_tmdb_status_map(db, items)
    tmdb_payload_map = load_tmdb_payload_map(db, items)
    # 一次 IN 查询取全部任务的引流摘要，避免列表 N+1。
    lead_in_map = load_lead_in_summary_map(db, [item.id for item in items])
    return [
        task_out(
            db,
            item,
            tmdb_status_map=tmdb_status_map,
            tmdb_payload_map=tmdb_payload_map,
            lead_in_map=lead_in_map,
        )
        for item in items
    ]


@router.post("/drama/stop-completed", response_model=StopCompletedDramaTasksOut, dependencies=[Depends(require_permissions(TASK_WRITE))])
def post_stop_completed_drama_tasks(request: Request, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    language, poster_language = get_tmdb_lang_pair(db)

    result = tasks_repo().stop_completed_drama_tasks(
        db, language=language, poster_language=poster_language
    )
    checked = result["checked"]
    matched_ids = result["matched_ids"]
    stopped = result["stopped"]

    audit.write_audit_log(
        db,
        actor_user_id=current.user.id,
        action="task.drama.stop_completed",
        target_type="task",
        target_id="drama",
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
        success=True,
        detail=f"checked={checked}, matched={len(matched_ids)}, stopped={stopped}",
    )
    commit(db)

    return StopCompletedDramaTasksOut(checked=int(checked or 0), matched=len(matched_ids), stopped=stopped, task_ids=matched_ids[:50])


@router.post('', response_model=TaskOut, dependencies=[Depends(require_permissions(TASK_WRITE))])
def post_task(request: Request, payload: TaskCreateIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    with transaction(db):
        task = create_task(db, **payload.model_dump())
        audit.write_audit_log(db, actor_user_id=current.user.id, action='task.create', target_type='task', target_id=str(task.id), ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'), success=True)
    db.refresh(task)
    # 新建任务必然没有执行记录，显式传空列表避免一次多余查询。
    return task_out(db, task, executions=[])


@router.patch('/{task_id:int}', response_model=TaskOut, dependencies=[Depends(require_permissions(TASK_WRITE))])
def patch_task(request: Request, task_id: int, payload: TaskUpdateIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    with transaction(db):
        task = update_task(db, task_id, **payload.model_dump(exclude_unset=True))
        audit.write_audit_log(db, actor_user_id=current.user.id, action='task.update', target_type='task', target_id=str(task_id), ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'), success=True)
    db.refresh(task)
    return task_out(db, task, executions=list_recent_executions(db, task_id, limit=3), lead_in_map=load_lead_in_summary_map(db, [task.id]))


@router.patch('/{task_id:int}/status', response_model=TaskOut, dependencies=[Depends(require_permissions(TASK_WRITE))])
def patch_task_status(request: Request, task_id: int, payload: TaskStatusIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    with transaction(db):
        task = set_task_enabled(db, task_id, payload.enabled)
        audit.write_audit_log(db, actor_user_id=current.user.id, action='task.status', target_type='task', target_id=str(task_id), ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'), success=True, detail=f'enabled={payload.enabled}')
    db.refresh(task)
    return task_out(db, task, executions=list_recent_executions(db, task_id, limit=3), lead_in_map=load_lead_in_summary_map(db, [task.id]))


@router.delete('/{task_id:int}', dependencies=[Depends(require_permissions(TASK_WRITE))])
def delete_task_by_id(request: Request, task_id: int, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    with transaction(db):
        delete_task(db, task_id)
        audit.write_audit_log(db, actor_user_id=current.user.id, action='task.delete', target_type='task', target_id=str(task_id), ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'), success=True)
    return {'ok': True}


@router.post('/{task_id:int}/run', response_model=TaskExecutionOut, dependencies=[Depends(require_permissions(TASK_RUN))])
def post_run_task(request: Request, task_id: int, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    with task_admission.reserve(task_id):
        task = get_task(db, task_id)
        execution = RunTaskUseCase(db, executor_cls=TaskExecutor).run(task, RunPolicy.for_trigger('http'))
        audit.write_audit_log(db, actor_user_id=current.user.id, action='task.run', target_type='task', target_id=str(task_id), ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'), success=execution.status == 'success', detail=execution.message)
        commit(db)
        db.refresh(execution)
        if str(getattr(task, "task_type", "") or "") == "drama":
            try:
                section, should_notify = build_task_section(task, execution)
                if should_notify:
                    send_runtime(db, DRAMA_NOTIFY_TITLE, section)
            except Exception:
                pass
        return execution_out(execution)


@router.post('/{task_id:int}/share', response_model=TaskShareLinkOut, dependencies=[Depends(require_permissions(TASK_WRITE))])
def post_create_task_share(request: Request, task_id: int, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    task = get_task(db, task_id)
    if task.account_name:
        account = get_active_account(db, task.account_name)
        if account is None:
            raise not_found('DRIVE_ACCOUNT_NOT_FOUND', '任务指定的网盘账号不存在或不可用')
    else:
        drive_type = AdapterRegistry.detect_drive_type(task.shareurl)
        if not drive_type:
            raise bad_request('TASK_SHAREURL_INVALID', '无法识别任务使用的网盘类型')
        account_name = pick_default_account_name(db, drive_type)
        account = get_active_account(db, account_name) if account_name else None
        if account is None:
            raise not_found('DRIVE_ACCOUNT_NOT_FOUND', '没有可用的网盘账号')

    adapter_class = AdapterFactory.ADAPTER_MAP.get(str(account.drive_type))
    if adapter_class is None or adapter_class.create_share_link is BaseCloudDriveAdapter.create_share_link:
        raise bad_request('TASK_SHARE_UNSUPPORTED', f'{account.drive_type}暂不支持生成分享链接')

    task_payload = {'shareurl': task.shareurl, 'account_name': account.name}
    manager = DatabaseAccountManager(db)
    manager.init_for_tasks([task_payload])
    adapter = manager.get_adapter_for_task(task_payload)
    if adapter is None or not adapter.is_active:
        raise bad_request('DRIVE_ACCOUNT_UNAVAILABLE', '网盘账号登录失败，请检查账号状态')

    try:
        result = adapter.create_share_link(task.savepath, task.taskname)
    except ValueError as exc:
        raise bad_request('TASK_SHARE_PATH_INVALID', str(exc)) from exc
    except NotImplementedError as exc:
        raise bad_request('TASK_SHARE_UNSUPPORTED', str(exc)) from exc
    except Exception as exc:
        logger.exception('生成任务分享链接失败 task_id=%s drive_type=%s', task_id, account.drive_type)
        raise bad_request('TASK_SHARE_FAILED', str(exc) or '生成分享链接失败') from exc

    share_url = str(result.get('share_url') or '').strip()
    if not share_url:
        raise bad_request('TASK_SHARE_FAILED', '网盘没有返回分享链接')
    title = str(result.get('title') or task.taskname)
    passcode = str(result.get('passcode') or '') or None
    expires_in_days = int(result['expires_in_days']) if result.get('expires_in_days') is not None else None
    # 生成的分享链接永久记录在任务上，下次直接查看/复制，不必重新生成。
    saved = save_task_share_link(
        task,
        share_url=share_url,
        share_id=str(result.get('share_id') or ''),
        title=title,
        drive_type=str(account.drive_type),
        account_name=str(account.name),
        passcode=passcode,
        expires_in_days=expires_in_days,
    )
    audit.write_audit_log(
        db, actor_user_id=current.user.id, action='task.share.create', target_type='task', target_id=str(task_id),
        ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'), success=True,
        detail=f'drive_type={account.drive_type}',
    )
    commit(db)
    return TaskShareLinkOut(
        share_url=share_url,
        share_id=str(result.get('share_id') or ''),
        title=title,
        drive_type=str(account.drive_type),
        account_name=str(account.name),
        passcode=passcode,
        expires_in_days=expires_in_days,
        saved_at=str(saved.get('saved_at') or '') or None,
    )


@router.get('/scheduler', response_model=TaskSchedulerSettingOut, dependencies=[Depends(require_permissions(TASK_READ))])
def get_task_scheduler_setting(db: Session = Depends(get_db)):
    setting = load_task_scheduler_setting(db)
    if setting is not None:
        # 纯读请求不提交、不 refresh：只回滚只读事务，省掉一次全行查询
        return scheduler_out(setting)
    with transaction(db):
        setting = get_or_create_task_scheduler_setting(db)
        payload = scheduler_out(setting)
    return payload


@router.patch('/scheduler', response_model=TaskSchedulerSettingOut, dependencies=[Depends(require_permissions(TASK_WRITE))])
def patch_task_scheduler_setting(request: Request, payload: TaskSchedulerSettingUpdateIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    with transaction(db):
        setting = update_task_scheduler_setting(db, **payload.model_dump(exclude_unset=True))
        audit.write_audit_log(db, actor_user_id=current.user.id, action='task.scheduler.update', target_type='task_scheduler_setting', target_id=str(setting.id), ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'), success=True)
    db.refresh(setting)
    task_scheduler_manager.reload()
    return scheduler_out(setting)


# 拆出去的子路由：分享预览/验证、网盘浏览/建目录/删除（路径与响应保持不变）
router.include_router(tasks_share.router)
router.include_router(tasks_drive.router)
router.include_router(tasks_stream.router)
