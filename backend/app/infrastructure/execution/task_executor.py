from __future__ import annotations

import json
import logging
import time
import traceback
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session
from treelib import Tree

from app.core.errors import bad_request
from app.infrastructure.drive.account_manager import DatabaseAccountManager
from app.infrastructure.execution.drama_executor import DramaTaskExecutor, SkipTask
from app.infrastructure.execution.execution_log import ExecutionLog
from app.models.task import Task
from app.models.task_execution import TaskExecution
from app.application.drama_share_autoupdate import is_auto_update_task, resolve_drama_shareurl_update
from app.application.generated_share_check import SNAPSHOT_KEY, check_generated_share
from app.application.shareurl_update_logs import append_shareurl_update_log


logger = logging.getLogger(__name__)

SHARE_SEARCH_ROUNDS = 4
SHARE_SEARCH_RETRY_DELAY = 2


def _truncate(s: str, max_len: int) -> str:
    s = str(s or "")
    if max_len <= 0:
        return ""
    if len(s) <= max_len:
        return s
    return f"{s[:max_len]}...(truncated,len={len(s)})"


@dataclass(slots=True)
class ExecutionPayload:
    status: str
    message: str | None
    tree_summary: str | None
    stage: str | None
    run_log: str | None
    adapter_snapshot: dict[str, Any]


class TaskExecutor:
    def __init__(self, db: Session):
        self.db = db

    @staticmethod
    def _task_to_dict(task: Task) -> dict[str, Any]:
        return {
            'task_uid': task.task_uid,
            'task_type': task.task_type,
            'taskname': task.taskname,
            'shareurl': task.shareurl,
            'savepath': task.savepath,
            'pattern': task.pattern or '',
            'replace': task.replace or '',
            'ignore_extension': task.ignore_extension,
            'account_name': task.account_name,
            'tmdb_id': task.tmdb_id,
            'tmdb_media_type': task.tmdb_media_type,
            'enabled': task.enabled,
            'addition': json.loads(task.addition_json) if task.addition_json else {},
            'extra': json.loads(task.extra_json) if task.extra_json else {},
        }

    def _execute_with_adapter(self, adapter: Any, task_data: dict[str, Any]) -> Tree:
        pwd_id, passcode, pdir_fid, _ = adapter.extract_url(task_data['shareurl'])
        token_response = adapter.get_stoken(pwd_id, passcode)
        if token_response.get('status') != 200:
            raise RuntimeError(token_response.get('message') or '获取分享 token 失败')
        stoken = token_response['data']['stoken']
        detail = adapter.get_detail(pwd_id, stoken, pdir_fid or '')
        items = (((detail or {}).get('data') or {}).get('list')) or []

        tree = Tree()
        tree.create_node(task_data['taskname'], 'root')
        if not items:
            tree.create_node('无可处理文件', 'empty', parent='root')
            return tree

        for index, item in enumerate(items[:200], start=1):
            node_id = f'node-{index}'
            name = item.get('file_name') or item.get('name') or item.get('fid') or f'item-{index}'
            tree.create_node(str(name), node_id, parent='root')
        return tree

    def run_task(
        self,
        task: Task,
        *,
        log: ExecutionLog | None = None,
        persist_execution: bool = True,
        account_manager: DatabaseAccountManager | None = None,
        init_account_for_task: bool = True,
        share_check_always_notify: bool = False,
        _auto_update_depth: int = 0,
        _tried_shareurls: set[str] | None = None,
        _generated_share_check: dict | None = None,
    ) -> TaskExecution:
        if not task.enabled:
            raise bad_request('TASK_DISABLED', '任务已禁用')

        log = log or ExecutionLog()
        log.set_stage("start")
        log.section("程序开始")
        log.line(f"执行时间: {log.started_at.strftime('%Y-%m-%d %H:%M:%S')}")
        log.line(f"任务名称: {task.taskname}")
        log.line(f"分享链接: {task.shareurl}")
        log.line(f"保存路径: {task.savepath}")
        log.line("")

        external_manager = account_manager is not None
        if account_manager is None:
            account_manager = DatabaseAccountManager(self.db)
        if persist_execution and task.id and task.task_type == "drama" and _auto_update_depth == 0:
            _generated_share_check = check_generated_share(
                self.db, task, account_manager, always_notify=share_check_always_notify
            )
            if _generated_share_check:
                log.section("分享链接检测")
                log.line(_generated_share_check["message"])
        share_snapshot = {SNAPSHOT_KEY: _generated_share_check} if _generated_share_check else {}
        task_data = self._task_to_dict(task)
        try:
            from app.application.magic_regex import get_enabled_magic_regex_map

            task_data["magic_regex"] = get_enabled_magic_regex_map(self.db)
        except Exception:
            task_data["magic_regex"] = {}

        if str(getattr(task, "task_type", "") or "") == "drama":
            extra = task_data.get("extra") or {}
            runweek_mode = str(extra.get("runweek_mode") or "manual").strip().lower()
            tmdb_id = int(task_data.get("tmdb_id") or 0)
            tmdb_media_type = str(task_data.get("tmdb_media_type") or "").strip().lower()
            task_data["tmdb_configured"] = False
            task_data["tmdb_update_weekdays"] = []
            task_data["tmdb_episode_weekdays"] = []
            if runweek_mode == "auto" and tmdb_id > 0 and tmdb_media_type == "tv":
                try:
                    from app.application.tmdb_cache import get_tmdb_detail_cached

                    configured, _detail, update_weekdays, episode_weekdays, _row = get_tmdb_detail_cached(
                        self.db, media_type="tv", tmdb_id=tmdb_id
                    )
                    task_data["tmdb_configured"] = bool(configured)
                    task_data["tmdb_update_weekdays"] = update_weekdays or []
                    task_data["tmdb_episode_weekdays"] = episode_weekdays or []
                except Exception:
                    task_data["tmdb_configured"] = False
                    task_data["tmdb_update_weekdays"] = []
                    task_data["tmdb_episode_weekdays"] = []



        if init_account_for_task:
            account_manager.init_for_tasks([task_data])
        adapter = account_manager.get_adapter_for_task(task_data)
        if adapter is None and external_manager:
            try:
                fallback_manager = DatabaseAccountManager(self.db)
                fallback_manager.init_for_tasks([task_data])
                account_manager = fallback_manager
                adapter = account_manager.get_adapter_for_task(task_data)
            except Exception:
                adapter = None
        started_at = log.started_at
        task_id = int(getattr(task, "id", 0) or 0)
        drama_executor = None

        if adapter is None:
            log.set_stage("select_account")
            log.section("验证账户")
            log.line("FAIL: 没有可用的驱动账号")
            log.set_stage("end")
            execution = TaskExecution(
                task_id=task_id,
                status='failed',
                started_at=started_at,
                finished_at=datetime.now(),
                message='没有可用的驱动账号',
                stage=log.stage,
                run_log=log.render(),
                adapter_snapshot=json.dumps(share_snapshot, ensure_ascii=False),
            )
            if not persist_execution:
                execution.id = 0
                return execution
            self.db.add(execution)
            self.db.flush()
            return execution

        try:
            log.set_stage("validate_account")
            log.section("验证账户")
            if not getattr(adapter, 'is_active', False):
                adapter.init()
            log.line(f"OK: 账户 '{getattr(adapter, 'nickname', '')}' ({getattr(adapter, 'DRIVE_TYPE', '')}) 已就绪")
            if task_data.get('task_type') == 'drama':
                log.set_stage("execute_drama")
                log.section("转存任务")
                drama_executor = DramaTaskExecutor(adapter=adapter, task_data=task_data, log=log)
                tree = drama_executor.execute()
            else:
                log.set_stage("execute_generic")
                log.section("转存任务")
                tree = self._execute_with_adapter(adapter, task_data)
            if persist_execution and task_id > 0 and task_data.get('task_type') == 'drama':
                # 已存进度：执行时顺手算出来的最大集数，写进任务 extra（不建快照表）
                saved_latest = getattr(drama_executor, "saved_latest", None)
                if isinstance(saved_latest, dict) and saved_latest:
                    try:
                        from app.application.task_share_links import merge_task_extra

                        merge_task_extra(task, {"saved_latest": saved_latest})
                        self.db.flush()
                        episode = saved_latest.get("episode")
                        if episode:
                            log.line(f"已存进度: 第{int(episode)}集")
                    except Exception:
                        logger.warning("写入已存进度失败 task_id=%s", task_id, exc_info=True)
            log.set_stage("end")
            log.section("程序结束")
            finished_at_local = datetime.now()
            duration_s = (finished_at_local - log.started_at).total_seconds()
            log.line(f"状态: success")
            log.line(f"运行时长: {duration_s:.2f}s")
            allow_once = bool((task_data.get("extra") or {}).get("allow_once"))
            if (
                persist_execution
                and task_id > 0
                and str(task_data.get("task_type") or "") == "drama"
                and allow_once
                and bool(getattr(task, "enabled", False))
            ):
                task.enabled = False
                self.db.flush()
                log.line("运行一次: 本次执行成功，已自动禁用任务")
            payload = ExecutionPayload(
                status='success',
                message='执行完成',
                tree_summary=str(tree),
                stage=log.stage,
                run_log=log.render(),
                adapter_snapshot={
                    'name': getattr(adapter, 'nickname', ''),
                    'drive_type': getattr(adapter, 'DRIVE_TYPE', ''),
                    'active': bool(getattr(adapter, 'is_active', False)),
                },
            )
        except Exception as exc:
            status = 'skipped' if isinstance(exc, SkipTask) else 'failed'
            stage = log.stage or "unknown"
            message = str(exc).strip() or type(exc).__name__

            # 链接失效时先尝试自动换链，而不是直接失败
            if (
                status == 'failed'
                and str(task_data.get("task_type") or "") == "drama"
                and stage in {"share_parse", "get_stoken", "fetch_share_items"}
                and is_auto_update_task(self.db, task, respect_toggle=True)
                and _auto_update_depth < 5
            ):
                if _tried_shareurls is None:
                    _tried_shareurls = set()
                try:
                    log.section("链接失效自动换链")
                    log.line(f"链接失效（阶段={stage}），尝试自动换链")
                    update_result = {}
                    for round_number in range(1, SHARE_SEARCH_ROUNDS + 1):
                        if round_number > 1:
                            time.sleep(SHARE_SEARCH_RETRY_DELAY)
                        old_shareurl = str(task.shareurl or '')
                        # 没换成功的探测结果不跨轮屏蔽，允许临时读取失败的候选重试。
                        round_tried = set(_tried_shareurls)
                        try:
                            update_result = resolve_drama_shareurl_update(
                                self.db, task, respect_toggle=True, tried_shareurls=round_tried,
                            )
                            updated = bool(update_result.get('updated'))
                            reason = str(update_result.get('reason') or '')
                            reason_text = {
                                'no_candidates': '未搜索到候选链接',
                                'no_valid_candidate': '候选链接均失效或不符合筛选条件',
                                'no_better_candidate': '未找到可读取的新链接',
                                'no_search_keyword': '缺少搜索关键词',
                                'not_applicable': '当前任务不适用自动换链',
                                'shareurl_empty': '源链接为空',
                            }.get(reason, reason or '未找到可用链接')
                            result_message = '已自动更换源链接' if updated else reason_text
                        except Exception as search_exc:
                            updated = False
                            update_result = {'checked': True, 'updated': False}
                            result_message = f'搜索异常：{str(search_exc).strip() or type(search_exc).__name__}'
                        entry = append_shareurl_update_log(
                            task, source='auto', status='success' if updated else 'failed',
                            message=result_message, old_shareurl=old_shareurl,
                            new_shareurl=str(update_result.get('new_shareurl') or ''), round_number=round_number,
                        )
                        log.line(f"自动换链 {round_number}/{SHARE_SEARCH_ROUNDS}: {entry['message']}")
                        if persist_execution and task_id > 0:
                            # 每轮独立落盘，刷新卡片即可看到；下一轮网络请求不占用写锁。
                            self.db.commit()
                        else:
                            self.db.flush()
                        if updated:
                            _tried_shareurls.update(round_tried)
                            break
                        if not update_result.get('checked'):
                            break
                    if bool(update_result.get("updated")):
                        log.line(f"OK: 自动换链成功 new={str(update_result.get('new_shareurl') or '').strip()}")
                        if persist_execution and task_id > 0:
                            self.db.refresh(task)
                        log.line("换链后立即再次执行任务")
                        re_execution = self.run_task(
                            task,
                            log=log,
                            persist_execution=persist_execution,
                            account_manager=account_manager,
                            init_account_for_task=False,
                            _auto_update_depth=_auto_update_depth + 1,
                            _tried_shareurls=_tried_shareurls,
                            _generated_share_check=_generated_share_check,
                        )
                        return re_execution
                    else:
                        log.line("本次自动换链搜索结束，未找到可用链接")
                except Exception as au_exc:
                    log.line(f"自动换链失败: {str(au_exc).strip() or type(au_exc).__name__}")

            try:
                logger.exception(
                    "任务执行异常 task_id=%s task_uid=%s task_type=%s stage=%s err=%s",
                    task_id,
                    str(getattr(task, "task_uid", "") or ""),
                    str(getattr(task, "task_type", "") or ""),
                    stage,
                    message,
                )
            except Exception:
                pass
            log.section("异常")
            log.line(f"阶段={stage}: {message}")

            log.set_stage(stage)
            log.section("程序结束")
            finished_at_local = datetime.now()
            duration_s = (finished_at_local - log.started_at).total_seconds()
            log.line(f"状态: {status}")
            log.line(f"运行时长: {duration_s:.2f}s")
            payload = ExecutionPayload(
                status=status,
                message=f"阶段={stage}：{message}",
                tree_summary=None,
                stage=stage,
                run_log=log.render(),
                adapter_snapshot={
                    'name': getattr(adapter, 'nickname', ''),
                    'drive_type': getattr(adapter, 'DRIVE_TYPE', ''),
                    'active': bool(getattr(adapter, 'is_active', False)),
                },
            )

        execution = TaskExecution(
            task_id=task_id,
            status=payload.status,
            started_at=started_at,
            finished_at=datetime.now(),
            tree_summary=payload.tree_summary,
            message=payload.message,
            stage=payload.stage,
            run_log=payload.run_log,
            adapter_snapshot=json.dumps({**payload.adapter_snapshot, **share_snapshot}, ensure_ascii=False),
        )
        if not persist_execution:
            execution.id = 0
            return execution
        self.db.add(execution)
        self.db.flush()
        if task_id > 0:
            try:
                from app.application.task_executions import prune_task_executions

                removed = prune_task_executions(self.db, task_id)
                if removed:
                    logger.info("清理旧执行记录 task_id=%s removed=%s", task_id, removed)
            except Exception:
                logger.warning("清理旧执行记录失败 task_id=%s", task_id, exc_info=True)
        return execution
