from app.application.dto.task_run import RunTaskCommand
from app.application.container import run_task_use_case
"""任务管理端点：任务 CRUD、执行、调度设置、模板与建议。

拆分说明：SSE 流式端点在 app.api.routes.tasks_stream，分享预览/验证在 tasks_share，
网盘浏览/建目录/删除在 tasks_drive；响应装配在 api/presenters（tmdb_view /
drive_payload / drive_accounts）。本模块通过 include_router 组合子路由，URL 与响应契约不变。
"""

import logging
from dataclasses import asdict

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.application.use_cases.run_task import RunPolicy
from app.api.deps import CurrentUser, get_current_user, require_permissions
from app.core.permissions import TASK_READ, TASK_RUN, TASK_WRITE
from app.application.container import commit, get_db, system_settings_repo, tasks_repo, transaction
from app.application.container import create_task_share_use_case
from app.application.dto.task_share import CreateTaskShareCommand
from app.application.container import task_scheduler as _acc_task_scheduler_manager; task_scheduler_manager = _acc_task_scheduler_manager()
from app.application.container import task_admission_service as _acc_task_admission; task_admission = _acc_task_admission()
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
from app.application.task_scheduler import get_or_create_task_scheduler_setting, load_task_scheduler_setting, update_task_scheduler_setting
from app.application.resource_search import fetch_task_suggestions
from app.application.tasks import create_task, delete_task, get_task, set_task_enabled, update_task
from app.application.tmdb_settings import get_tmdb_lang_pair

from app.api.routes import task_saved_progress, tasks_drive, tasks_share, tasks_stream
from app.api.presenters.task import execution_out, scheduler_out, task_out
from app.application.queries.task_views import (
    execution_view,
    list_task_views,
    scheduler_view,
    task_detail_view,
    task_view,
)

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
    from app.application.resource_search import fetch_task_suggestions_result

    dt = str(drive_type or "").strip() or None
    result = fetch_task_suggestions_result(db, keyword=q, deep=d, drive_type=dt, search_filter=sf, search_exclude=se, search_date_from=df, search_filter_mode=sfm, search_exclude_mode=sem)
    items = result.items
    if result.changed:
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
    return TaskSuggestionListOut(success=True, data=items, message=result.message, partial=result.partial, warnings=result.warnings)




@router.get('', response_model=list[TaskOut], dependencies=[Depends(require_permissions(TASK_READ))])
def get_tasks(db: Session = Depends(get_db)):
    # 查询口径（执行记录 3 条 + TMDB/引流批量装配）收在 application/queries，路由只做映射。
    return [task_out(view) for view in list_task_views(db, executions_per_task=3)]


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
    return task_out(task_view(db, task, executions=[]))


@router.patch('/{task_id:int}', response_model=TaskOut, dependencies=[Depends(require_permissions(TASK_WRITE))])
def patch_task(request: Request, task_id: int, payload: TaskUpdateIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    with transaction(db):
        task = update_task(db, task_id, **payload.model_dump(exclude_unset=True))
        audit.write_audit_log(db, actor_user_id=current.user.id, action='task.update', target_type='task', target_id=str(task_id), ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'), success=True)
    db.refresh(task)
    return task_out(task_detail_view(db, task))


@router.patch('/{task_id:int}/status', response_model=TaskOut, dependencies=[Depends(require_permissions(TASK_WRITE))])
def patch_task_status(request: Request, task_id: int, payload: TaskStatusIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    with transaction(db):
        task = set_task_enabled(db, task_id, payload.enabled)
        audit.write_audit_log(db, actor_user_id=current.user.id, action='task.status', target_type='task', target_id=str(task_id), ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'), success=True, detail=f'enabled={payload.enabled}')
    db.refresh(task)
    return task_out(task_detail_view(db, task))


@router.delete('/{task_id:int}', dependencies=[Depends(require_permissions(TASK_WRITE))])
def delete_task_by_id(request: Request, task_id: int, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    with transaction(db):
        delete_task(db, task_id)
        audit.write_audit_log(db, actor_user_id=current.user.id, action='task.delete', target_type='task', target_id=str(task_id), ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'), success=True)
    return {'ok': True}


@router.post('/{task_id:int}/run', response_model=TaskExecutionOut, dependencies=[Depends(require_permissions(TASK_RUN))])
def post_run_task(request: Request, task_id: int, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    with task_admission.reserve(task_id):
        command = RunTaskCommand(task_id, actor_user_id=current.user.id,
            ip=request.client.host if request.client else None,
            user_agent=request.headers.get('user-agent'))
        execution = run_task_use_case(db).execute(command, RunPolicy.for_trigger('http'))
        return execution_out(execution_view(execution))


@router.post('/{task_id:int}/share', response_model=TaskShareLinkOut, dependencies=[Depends(require_permissions(TASK_WRITE))])
def post_create_task_share(request: Request, task_id: int, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    command = CreateTaskShareCommand(
        task_id=task_id, actor_user_id=current.user.id,
        ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'),
    )
    return TaskShareLinkOut(**asdict(create_task_share_use_case(db).execute(command)))


@router.get('/scheduler', response_model=TaskSchedulerSettingOut, dependencies=[Depends(require_permissions(TASK_READ))])
def get_task_scheduler_setting(db: Session = Depends(get_db)):
    setting = load_task_scheduler_setting(db)
    if setting is not None:
        # 纯读请求不提交、不 refresh：只回滚只读事务，省掉一次全行查询
        return scheduler_out(scheduler_view(setting))
    with transaction(db):
        setting = get_or_create_task_scheduler_setting(db)
        payload = scheduler_out(scheduler_view(setting))
    return payload


@router.patch('/scheduler', response_model=TaskSchedulerSettingOut, dependencies=[Depends(require_permissions(TASK_WRITE))])
def patch_task_scheduler_setting(request: Request, payload: TaskSchedulerSettingUpdateIn, current: CurrentUser = Depends(get_current_user), db: Session = Depends(get_db)):
    with transaction(db):
        setting = update_task_scheduler_setting(db, **payload.model_dump(exclude_unset=True))
        audit.write_audit_log(db, actor_user_id=current.user.id, action='task.scheduler.update', target_type='task_scheduler_setting', target_id=str(setting.id), ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'), success=True)
    db.refresh(setting)
    task_scheduler_manager.reload()
    return scheduler_out(scheduler_view(setting))


# 拆出去的子路由：分享预览/验证、网盘浏览/建目录/删除（路径与响应保持不变）
router.include_router(tasks_share.router)
router.include_router(tasks_drive.router)
router.include_router(tasks_stream.router)
router.include_router(task_saved_progress.router)
