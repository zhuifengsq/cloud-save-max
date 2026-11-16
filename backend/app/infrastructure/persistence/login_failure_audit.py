"""One independent transaction for each bounded failure-audit batch."""
import json
from app.infrastructure.persistence.session import SessionLocal, transaction
from app.infrastructure.persistence.repositories.audit import add_audit_log


def write_batch(batch: list[tuple[str, int]]) -> None:
    with SessionLocal() as db, transaction(db):
        for ip, count in batch:
            add_audit_log(db, actor_user_id=None, action='auth.login', ip=ip,
                          success=False, detail=json.dumps({'failure_count': count}))
