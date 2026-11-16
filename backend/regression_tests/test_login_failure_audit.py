from app.application.login_failure_audit import LoginFailureAudit


def test_failures_are_aggregated_without_request_database_work():
    writes = []
    audit = LoginFailureAudit(writer=lambda batch: writes.append(batch), autostart=False)
    for _ in range(100):
        audit.record('ip')
    assert writes == []
    assert audit.flush()
    assert writes == [[('ip', 100)]]


def test_capacity_is_bounded_and_dropped_failures_are_diagnosed(caplog):
    audit = LoginFailureAudit(writer=lambda _: None, capacity=2, autostart=False)
    assert audit.record('a')
    assert audit.record('b')
    assert not audit.record('c')
    assert 'capacity' in caplog.text
    assert audit.flush()
    assert audit.record('c')


def test_failed_batch_is_not_retried_without_bound_and_logs_no_exception_secret(caplog):
    def broken(batch):
        raise RuntimeError('fake-secret')
    audit = LoginFailureAudit(writer=broken, autostart=False)
    audit.record('a')
    assert not audit.flush()
    assert 'failed' in caplog.text
    assert 'fake-secret' not in caplog.text


def test_real_sqlite_writer_uses_one_independent_commit_for_many_failures(tmp_path, monkeypatch):
    import json
    from sqlalchemy import create_engine, event, select
    from sqlalchemy.orm import sessionmaker
    from app.infrastructure.persistence.base import Base
    import app.infrastructure.persistence.models
    from app.infrastructure.persistence.models.audit_log import AuditLog
    from app.infrastructure.persistence import login_failure_audit as writer
    engine = create_engine('sqlite:///' + (tmp_path / 'audit.db').as_posix())
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    commits = []
    event.listen(sessions, 'after_commit', lambda _: commits.append(1))
    monkeypatch.setattr(writer, 'SessionLocal', sessions)
    sink = LoginFailureAudit(writer=writer.write_batch, autostart=False)
    for i in range(100):
        sink.record('first' if i < 90 else 'second')
    assert commits == []
    assert sink.flush()
    assert commits == [1]
    with sessions() as db:
        rows = db.scalars(select(AuditLog)).all()
        assert {r.ip: json.loads(r.detail)['failure_count'] for r in rows} == {'first': 90, 'second': 10}
    engine.dispose()


def test_worker_start_failure_does_not_escape_or_disable_future_retries(monkeypatch, caplog):
    from app.application import login_failure_audit as module
    now = [0.0]
    sink = LoginFailureAudit(writer=lambda _: None, clock=lambda: now[0])
    original = module.Thread.start
    attempts = []
    def start(thread):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError('fake-sensitive-start-error')
        original(thread)
    monkeypatch.setattr(module.Thread, 'start', start)
    assert sink.record('a')
    for _ in range(10):
        assert sink.record('a')
    assert len(attempts) == 1
    assert 'start failed' in caplog.text
    assert 'fake-sensitive' not in caplog.text
    now[0] = 6
    assert sink.record('a')
    assert len(attempts) == 2
    sink.close()


def test_service_is_rebuilt_after_prior_lifecycle_shutdown():
    from app.application.container import login_failure_audit_service
    old = login_failure_audit_service()
    old.close()
    fresh = login_failure_audit_service()
    assert fresh is not old
    assert not fresh.closed
