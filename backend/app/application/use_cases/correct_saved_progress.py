"""Read the saved directory without a DB transaction, then conditionally save."""
from app.application import audit
from app.core.runtime.tx import end_read_tx
from app.domain.ports.saved_progress import SavedProgressCorrection


class CorrectSavedProgressUseCase:
    def __init__(self, db, *, store=None, scanner=None, admission=None):
        from app.application.container import saved_progress_store, saved_progress_scanner, task_admission_service
        self.db = db
        self.store = store if store is not None else saved_progress_store(db)
        self.scanner = scanner if scanner is not None else saved_progress_scanner()
        self.admission = admission if admission is not None else task_admission_service()

    def execute(self, task_id: int, *, actor_user_id=None, ip=None, user_agent=None) -> SavedProgressCorrection:
        with self.admission.reserve(task_id):
            if self.db.new or self.db.dirty or self.db.deleted:
                raise RuntimeError('Correction cannot start with uncommitted writes')
            plan = self.store.prepare(task_id)
            end_read_tx(self.db)
            scan = self.scanner.scan(plan)
            previous = plan.task_data.get('extra', {}).get('saved_latest') or {}
            previous_episode = previous.get('episode') if isinstance(previous, dict) else None
            previous_episode = int(previous_episode) if isinstance(previous_episode, (int, float)) else None
            try:
                self.store.save(plan, scan)
                audit.write_audit_log(self.db, actor_user_id=actor_user_id,
                    action='task.saved_progress.correct', target_type='task', target_id=str(task_id),
                    ip=ip, user_agent=user_agent, success=True,
                    detail=f'episode={previous_episode}->{scan.saved_latest.episode};files={scan.scanned_files};directories={scan.scanned_directories}')
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise
            return SavedProgressCorrection(task_id, previous_episode, scan.saved_latest, scan.scanned_files, scan.scanned_directories)
