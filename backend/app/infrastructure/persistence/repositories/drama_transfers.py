from collections.abc import Callable
from dataclasses import asdict, dataclass
from uuid import uuid4

from sqlalchemy import delete, update
from sqlalchemy.orm import Session

from app.core.log_redaction import redact_log_text
from app.core.settings import settings
from app.infrastructure.persistence.models.drama_transfer_checkpoint import DramaTransferCheckpoint

CHECKPOINT_SCOPE_CHANGED = "任务配置已变更，已有转存待确认"
CHECKPOINT_RENAME_CHANGED = "重命名规则已变更，已有转存待确认"
CONFIGURATION_CHANGED = "configuration_changed"


@dataclass
class TransferBatch:
    destination: str
    # Source fid, original name and target name. Never store share tokens.
    files: list[tuple[str, str, str]]
    is_dir: bool = False
    defer_rename: bool = False
    task_id: str = ""
    saved_fids: list[str] | None = None
    output_fids: list[str] | None = None
    rename_signature: str = ""


def configuration_blocks_recovery(recovery: dict) -> bool:
    return recovery.get("kind") == CONFIGURATION_CHANGED or recovery.get("reason") in (
        CHECKPOINT_SCOPE_CHANGED, CHECKPOINT_RENAME_CHANGED,
    )


def can_retry_confirmation(row: DramaTransferCheckpoint) -> bool:
    if configuration_blocks_recovery(row.recovery):
        return False
    if row.batch is not None:
        return bool(row.batch.get("task_id") or row.batch.get("output_fids"))
    if row.renames_started and not row.confirmed:
        return False
    return bool(row.confirmed)


def describe_checkpoint(db: Session, task_id: int) -> dict | None:
    row = db.get(DramaTransferCheckpoint, task_id)
    if row is None:
        return None
    batch = row.batch or {}
    retryable = can_retry_confirmation(row)
    paused_fallback_dirs = [
        name for name in row.fallback_dirs
        if row.fallback_missing.get(name, 0) >= settings.drama_fallback_missing_limit
    ]
    confirmed_files = []
    confirmed_count = 0
    for confirmed in row.confirmed:
        fids = confirmed.get("saved_fids") or []
        confirmed_count += len(fids)
        for fid, (_, origin, target) in zip(fids, confirmed["files"]):
            if len(confirmed_files) == 100:
                break
            confirmed_files.append({"fid": fid, "name": origin, "target_name": target})
    reason = row.recovery.get("reason")
    if reason and configuration_blocks_recovery(row.recovery):
        reason += "。恢复原配置后，下次执行会继续处理"
    if not reason:
        if row.renames_started:
            reason = "已转存，等待重命名校验"
        elif paused_fallback_dirs:
            reason = "部分分享目录连续缺失，等待处理"
        elif row.batch is None and row.fallback_excluded:
            reason = "部分目录按当前规则排除"
        elif row.batch is None and row.fallback_dirs:
            reason = "有目录等待继续同步"
        else:
            reason = "转存结果待确认"
    return {
        "version": row.version,
        "reason": reason,
        "attempts": row.recovery.get("attempts", 0),
        "requires_confirmation": bool(row.recovery.get("blocked") or paused_fallback_dirs)
        or (row.batch is not None and not retryable),
        "can_retry": retryable,
        "pending_names": [origin for _, origin, _ in batch.get("files", [])],
        "output_fids": batch.get("output_fids") or [],
        "fallback_dirs": row.fallback_dirs,
        "paused_fallback_dirs": paused_fallback_dirs,
        "excluded_fallback_dirs": row.fallback_excluded,
        "discardable_fallback_dirs": [
            name for name in row.fallback_dirs
            if row.batch is None or name in paused_fallback_dirs or name in row.fallback_excluded
        ],
        "confirmed_count": confirmed_count,
        "confirmed_files": confirmed_files,
    }


def resolve_checkpoint(db: Session, task_id: int, version: str, *, abandon: bool, directory: str | None = None) -> bool:
    row = db.get(DramaTransferCheckpoint, task_id)
    if row is None or row.version != version:
        return False
    condition = (DramaTransferCheckpoint.task_id == task_id, DramaTransferCheckpoint.version == version)
    if directory is not None:
        if directory not in row.fallback_dirs:
            return False
        if (row.batch is not None and directory not in row.fallback_excluded
                and row.fallback_missing.get(directory, 0) < settings.drama_fallback_missing_limit):
            return False
        remaining = [name for name in row.fallback_dirs if name != directory]
        if not remaining and row.batch is None and not row.confirmed and not row.renames_started:
            return db.execute(delete(DramaTransferCheckpoint).where(*condition)).rowcount == 1
        missing = {name: count for name, count in row.fallback_missing.items() if name != directory}
        excluded = [name for name in row.fallback_excluded if name != directory]
        return db.execute(update(DramaTransferCheckpoint).where(*condition)
                          .values(fallback_dirs=remaining, fallback_missing=missing,
                                  fallback_excluded=excluded, version=uuid4().hex)).rowcount == 1
    if abandon:
        return bool(db.execute(delete(DramaTransferCheckpoint).where(*condition)).rowcount)
    if not can_retry_confirmation(row):
        return False
    return bool(db.execute(update(DramaTransferCheckpoint).where(*condition)
                           .values(recovery={}, version=uuid4().hex)).rowcount)


class DramaTransferStore:
    """Short, independent transactions that finish before each cloud operation."""

    def __init__(self, session: Callable[[], Session], task_id: int):
        self._session = session
        self._task_id = task_id
        self._verified_scope: str | None = None

    def load(self) -> DramaTransferCheckpoint | None:
        with self._session() as db:
            row = db.get(DramaTransferCheckpoint, self._task_id)
            if row is not None:
                db.expunge(row)
            return row

    def begin_batch(self, scope: str, batch: TransferBatch) -> None:
        with self._session() as db, db.begin():
            row = self._get_or_create(db, scope)
            if row.batch is not None or row.renames_started:
                raise RuntimeError("已有夸克转存批次待确认")
            row.batch = asdict(batch)
            row.recovery = {}

    def update_batch(self, scope: str, batch: TransferBatch | None) -> None:
        with self._session() as db, db.begin():
            row = self._get_or_create(db, scope)
            row.batch = asdict(batch) if batch is not None else None
            row.recovery = {}
            if row.batch is None and not row.fallback_dirs and not row.confirmed:
                db.delete(row)

    def confirm_normal_batch(self, scope: str, batch: TransferBatch) -> None:
        with self._session() as db, db.begin():
            row = self._get_or_create(db, scope)
            row.confirmed = [*row.confirmed, asdict(batch)]
            row.batch = None
            row.recovery = {}

    def record_problem(self, scope: str, reason: str, *, manual: bool = False, kind: str | None = None) -> str:
        reason = redact_log_text(reason)[:240]
        with self._session() as db, db.begin():
            row = self._get_or_create(db, scope)
            attempts = int(row.recovery.get("attempts", 0)) + 1
            blocked = manual or attempts >= 3
            row.recovery = {"reason": reason, "attempts": attempts, "blocked": blocked}
            if kind is not None:
                row.recovery = {**row.recovery, "kind": kind}
        return f"需人工确认：{reason}" if blocked else reason

    def clear_configuration_problem(self, scope: str) -> None:
        with self._session() as db, db.begin():
            row = self._get_or_create(db, scope)
            if configuration_blocks_recovery(row.recovery):
                row.recovery = {}

    def remember_fallback_scope(self, scope: str, fallback_scope: str) -> None:
        with self._session() as db, db.begin():
            row = self._get_or_create(db, scope)
            if row.fallback_dirs and row.fallback_scope is None:
                row.fallback_scope = fallback_scope

    def adopt_fallback_scope(self, previous_scope: str, scope: str, fallback_scope: str) -> bool:
        with self._session() as db, db.begin():
            row = db.get(DramaTransferCheckpoint, self._task_id)
            if (row is None or row.scope != previous_scope or row.fallback_scope != fallback_scope
                    or not row.fallback_dirs or row.batch is not None or row.confirmed or row.renames_started):
                return False
            if row.recovery.get("blocked") and not configuration_blocks_recovery(row.recovery):
                return False
            row.scope = scope
            row.version = uuid4().hex
            if configuration_blocks_recovery(row.recovery):
                row.recovery = {}
            return True

    def start_renames(self, scope: str) -> None:
        with self._session() as db, db.begin():
            self._get_or_create(db, scope).renames_started = True

    def verify_renames(self, scope: str) -> None:
        self._verified_scope = scope

    def acknowledge_renames(self, db: Session) -> None:
        if self._verified_scope is not None:
            # The successful execution record and checkpoint removal share one commit.
            db.execute(delete(DramaTransferCheckpoint).where(
                DramaTransferCheckpoint.task_id == self._task_id,
                DramaTransferCheckpoint.scope == self._verified_scope,
                DramaTransferCheckpoint.renames_started.is_(True),
                DramaTransferCheckpoint.batch.is_(None),
            ))

    def set_fallback_dir(self, scope: str, directory: str, *, active: bool, fallback_scope: str | None = None) -> None:
        with self._session() as db, db.begin():
            row = self._get_or_create(db, scope)
            directories = set(row.fallback_dirs)
            if active:
                directories.add(directory)
                if row.fallback_scope is None:
                    row.fallback_scope = fallback_scope
            else:
                directories.discard(directory)
                if directory in row.fallback_missing:
                    row.fallback_missing = {
                        name: count for name, count in row.fallback_missing.items() if name != directory
                    }
                if directory in row.fallback_excluded:
                    row.fallback_excluded = [name for name in row.fallback_excluded if name != directory]
            row.fallback_dirs = sorted(directories)
            if not row.fallback_dirs and row.batch is None and not row.confirmed:
                db.delete(row)

    def set_fallback_missing(self, scope: str, counts: dict[str, int], *, excluded: set[str] | None = None) -> None:
        with self._session() as db, db.begin():
            row = self._get_or_create(db, scope)
            row.fallback_missing = {name: counts[name] for name in row.fallback_dirs if name in counts}
            if excluded is not None:
                row.fallback_excluded = [name for name in row.fallback_dirs if name in excluded]

    def _get_or_create(self, db: Session, scope: str) -> DramaTransferCheckpoint:
        row = db.get(DramaTransferCheckpoint, self._task_id)
        if row is None:
            row = DramaTransferCheckpoint(task_id=self._task_id, scope=scope, fallback_dirs=[], fallback_missing={},
                                          fallback_excluded=[],
                                          confirmed=[], renames_started=False, recovery={})
            db.add(row)
        elif row.scope != scope:
            raise RuntimeError("任务配置已变更，原夸克转存仍待确认")
        row.version = uuid4().hex
        return row
