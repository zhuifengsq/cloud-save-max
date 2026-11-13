"""Conditional progress persistence and read-only enumeration of saved files."""
import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import PurePosixPath

from sqlalchemy import case, func, select, update
from sqlalchemy.orm import noload

from app.application.magic_regex import get_enabled_magic_regex_map
from app.core.errors import ApiError, bad_request, not_found
from app.domain.ports.saved_progress import SavedLatestProgress, SavedProgressPlan, SavedProgressScan
from app.domain.ports.task_share import TaskSharePlan
from app.infrastructure.drive.adapter_registry import AdapterRegistry
from app.infrastructure.drive.factory import AdapterFactory
from app.infrastructure.drive.gateway import _get_fid, _get_name, _is_dir
from app.infrastructure.execution.drama_progress import calculate_saved_latest
from app.infrastructure.persistence.models.task import Task
from app.infrastructure.persistence.models.drive_account import DriveAccount
from app.infrastructure.persistence.models.magic_regex_rule import MagicRegexRule
from app.infrastructure.persistence.repositories import drive_accounts

_TASK_FIELDS = ('task_type', 'taskname', 'shareurl', 'savepath', 'account_name', 'pattern', 'replace',
                'ignore_extension', 'addition_json', 'extra_json', 'tmdb_id', 'tmdb_media_type')
_NON_EPISODE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp', '.gif', '.svg', '.ico', '.txt', '.md', '.nfo', '.json', '.url', '.ass', '.srt', '.sub', '.pdf', '.db', '.ini', '.log', '.torrent'}
_ACCOUNT_FIELDS = ('id', 'name', 'drive_type', 'cookie', 'config_json', 'enabled', 'is_default', 'runtime_status')
_MAGIC_FIELDS = ('id', 'key', 'label', 'pattern', 'replace', 'enabled')


class SqlSavedProgressStore:
    def __init__(self, db):
        self.db = db

    def prepare(self, task_id: int) -> SavedProgressPlan:
        task = self.db.scalar(select(Task).options(noload(Task.executions)).where(Task.id == task_id))
        if task is None:
            raise not_found('TASK_NOT_FOUND', '任务不存在')
        if task.task_type != 'drama':
            raise bad_request('TASK_NOT_DRAMA', '仅追剧任务支持校正已存进度')
        if task.account_name:
            account = (drive_accounts.find_active_by_name(self.db, name=task.account_name)
                       or drive_accounts.find_enabled_by_name(self.db, name=task.account_name))
        else:
            drive_type = AdapterRegistry.detect_drive_type(task.shareurl)
            account = (drive_accounts.find_active_by_type(self.db, drive_type=drive_type)
                       or drive_accounts.find_enabled_by_type(self.db, drive_type=drive_type)) if drive_type else None
        if account is None:
            raise not_found('DRIVE_ACCOUNT_NOT_FOUND', '任务指定或默认网盘账号不存在或不可用')
        snapshot = {key: getattr(task, key) for key in _TASK_FIELDS}
        try:
            extra, addition = json.loads(task.extra_json or '{}'), json.loads(task.addition_json or '{}')
        except (TypeError, ValueError) as exc:
            raise bad_request('TASK_CONFIG_INVALID', '任务配置无效，请检查后重试') from exc
        if not isinstance(extra, dict) or not isinstance(addition, dict):
            raise bad_request('TASK_CONFIG_INVALID', '任务配置无效，请检查后重试')
        drive = TaskSharePlan(task.id, task.taskname, task.savepath, task.shareurl, task.account_name,
            account.name, account.drive_type,
            AdapterRegistry.parse_config_json(account.drive_type, account.config_json, account.cookie), account.cookie)
        data = {'taskname': task.taskname, 'pattern': task.pattern, 'replace': task.replace,
                'extra': extra, 'addition': addition, 'magic_regex': get_enabled_magic_regex_map(self.db)}
        account_snapshot = {key: getattr(account, key) for key in _ACCOUNT_FIELDS}
        magic_snapshot = tuple(dict(row) for row in self.db.execute(
            select(*(getattr(MagicRegexRule, key) for key in _MAGIC_FIELDS)).order_by(MagicRegexRule.id)
        ).mappings())
        return SavedProgressPlan(task.id, drive, snapshot, data, account_snapshot, magic_snapshot)

    def save(self, plan: SavedProgressPlan, scan: SavedProgressScan) -> None:
        self.db.expire_all()
        try:
            current = self.prepare(plan.task_id)
        except ApiError as exc:
            raise ApiError('TASK_CHANGED', '任务或账号已变更，请重新校正', 409) from exc
        if current != plan:
            raise ApiError('TASK_CHANGED', '任务、识别规则或网盘账号已变更，请重新校正', 409)
        extra = dict(current.task_data['extra'])
        extra['saved_latest'] = asdict(scan.saved_latest)
        statement = update(Task).where(Task.id == plan.task_id)
        for key, value in plan.task_snapshot.items():
            statement = statement.where(getattr(Task, key) == value)
        # These predicates run in the UPDATE itself. A SELECT-only recheck has
        # a race if account/default/rule configuration changes immediately after it.
        account_exists = select(DriveAccount.id)
        for key, value in plan.account_snapshot.items():
            account_exists = account_exists.where(getattr(DriveAccount, key) == value)
        preferred_account = select(DriveAccount.id).where(DriveAccount.enabled.is_(True))
        if plan.drive.task_account_name:
            preferred_account = preferred_account.where(DriveAccount.name == plan.drive.task_account_name)
        else:
            preferred_account = preferred_account.where(DriveAccount.drive_type == plan.drive.drive_type)
        preferred_account = preferred_account.order_by(
            case((DriveAccount.runtime_status == 'active', 0), else_=1),
            DriveAccount.is_default.desc(), DriveAccount.id.asc(),
        ).limit(1).scalar_subquery()
        statement = statement.where(account_exists.exists(), preferred_account == plan.account_snapshot['id'])
        statement = statement.where(select(func.count(MagicRegexRule.id)).scalar_subquery() == len(plan.magic_snapshot))
        for rule in plan.magic_snapshot:
            exists = select(MagicRegexRule.id)
            for key, value in rule.items():
                exists = exists.where(getattr(MagicRegexRule, key) == value)
            statement = statement.where(exists.exists())
        written = self.db.execute(statement.values(extra_json=json.dumps(extra, ensure_ascii=False)), execution_options={'synchronize_session': False})
        if written.rowcount != 1:
            raise ApiError('TASK_CHANGED', '任务已变更，请重新校正', 409)


def _open_adapter(plan: SavedProgressPlan):
    drive = plan.drive
    adapter = AdapterFactory.create_adapter(drive.drive_type, drive.cookie, config=dict(drive.config), account_name=drive.account_name)
    if adapter is None or (not adapter.is_active and not adapter.init()):
        raise ApiError('DRIVE_ACCOUNT_UNAVAILABLE', '网盘账号登录失败，请检查账号状态', 502)
    # Instances belong to AdapterFactory's bounded cache, which owns closing.
    return adapter


class SavedProgressScanner:
    MAX_DIRECTORIES = 200
    MAX_FILES = 10000
    MAX_DEPTH = 12
    MAX_SECONDS = 35

    def __init__(self, *, adapter_factory=_open_adapter):
        self.adapter_factory = adapter_factory

    def scan(self, plan: SavedProgressPlan) -> SavedProgressScan:
        path = str(plan.drive.savepath or '').strip().replace('\\', '/')
        parts = [part for part in path.split('/') if part and part != '.']
        if not parts or '..' in parts or '://' in path:
            raise bad_request('SAVED_PROGRESS_PATH_INVALID', '请为任务选择独立的剧集保存目录，不能校正网盘根目录')
        started = time.monotonic()
        adapter = self.adapter_factory(plan)
        reads = 0

        def read(fid):
            nonlocal reads
            reads += 1
            if reads > self.MAX_DIRECTORIES + len(parts) or time.monotonic() - started > self.MAX_SECONDS:
                raise ApiError('SAVED_PROGRESS_SCAN_LIMIT', '目录扫描达到限制，原已存进度保留', 422)
            try:
                response = adapter.ls_dir_complete(str(fid))
            except Exception as exc:
                raise ApiError('SAVED_PROGRESS_READ_FAILED', '目录未能完整读取，原已存进度保留，请稍后重试', 502) from exc
            if time.monotonic() - started > self.MAX_SECONDS:
                raise ApiError('SAVED_PROGRESS_SCAN_LIMIT', '目录扫描超时，原已存进度保留', 422)
            if not isinstance(response, dict) or str(response.get('code')) not in ('0', '200'):
                raise ApiError('SAVED_PROGRESS_READ_FAILED', '目录未能完整读取，原已存进度保留，请稍后重试', 502)
            entries = (response.get('data') or {}).get('list')
            if not isinstance(entries, list) or any(not isinstance(item, dict) or not _get_fid(item) or not _get_name(item) for item in entries):
                raise ApiError('SAVED_PROGRESS_READ_FAILED', '目录数据不完整，原已存进度保留', 502)
            if len({_get_fid(item) for item in entries}) != len(entries):
                raise ApiError('SAVED_PROGRESS_READ_FAILED', '目录读取出现重复条目，原已存进度保留', 502)
            total = (response.get('metadata') or {}).get('_total')
            if total is not None and int(total) != len(entries):
                raise ApiError('SAVED_PROGRESS_READ_FAILED', '目录分页尚未完整读取，原已存进度保留', 502)
            return entries

        fid = '0'
        ancestors = {fid}
        for part in parts:
            matches = [item for item in read(fid) if _is_dir(item) and _get_name(item) == part]
            if len(matches) != 1:
                raise ApiError('SAVED_PROGRESS_PATH_NOT_FOUND', '保存目录不存在或无法唯一定位，原已存进度保留', 422)
            fid = _get_fid(matches[0])
            if fid in ancestors:
                raise ApiError('SAVED_PROGRESS_READ_FAILED', '目录结构异常，原已存进度保留', 502)
            ancestors.add(fid)

        queue = [(fid, 0)]
        visited = set()
        names = []
        scanned_files = 0
        addition = plan.task_data['addition']
        excluded = [word.strip().casefold() for word in str(addition.get('folder_exclude') or '').split('|') if word.strip()]
        exclude_match = all if str(addition.get('folder_exclude_mode') or 'any') == 'all' else any
        while queue:
            directory, depth = queue.pop()
            if directory in visited or depth > self.MAX_DEPTH or len(visited) >= self.MAX_DIRECTORIES:
                raise ApiError('SAVED_PROGRESS_SCAN_LIMIT', '目录结构异常或扫描达到限制，原已存进度保留', 422)
            visited.add(directory)
            for entry in read(directory):
                name = _get_name(entry)
                if _is_dir(entry):
                    if excluded and exclude_match(word in name.casefold() for word in excluded):
                        continue
                    queue.append((_get_fid(entry), depth + 1))
                else:
                    scanned_files += 1
                    if scanned_files > self.MAX_FILES:
                        raise ApiError('SAVED_PROGRESS_SCAN_LIMIT', '文件数量达到扫描限制，原已存进度保留', 422)
                    if PurePosixPath(name).suffix.casefold() not in _NON_EPISODE_EXTENSIONS:
                        names.append(name)
        at = datetime.now(timezone.utc).isoformat(timespec='seconds')
        try:
            latest = calculate_saved_latest(plan.task_data, names, [], at=at, preserve_previous=False, require_all_recognized=True)
        except ValueError as exc:
            raise ApiError('SAVED_PROGRESS_UNRECOGNIZED', '部分文件无法识别集号，原已存进度保留；请检查文件名或识别规则', 422) from exc
        if names and latest is None:
            raise ApiError('SAVED_PROGRESS_UNRECOGNIZED', '文件已读取，但无法识别集号，原已存进度保留；请检查文件名或识别规则', 422)
        latest = latest or {'episode': 0, 'season': None, 'name': '', 'at': at}
        return SavedProgressScan(SavedLatestProgress(**latest), scanned_files, len(visited))
